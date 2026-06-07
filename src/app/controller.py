"""主窗口控制模块。

本文件是桌面端最核心的界面协调层，负责加载主窗口与对话框 UI，
连接控件事件、调用业务服务、管理后台线程任务、驱动地图展示，
并处理结果历史、详情缓存和聊天助手等交互流程。
"""


from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import re
import time
from typing import Type, TypeVar

from PySide6.QtCore import QDateTime, QFile, QObject, QStringListModel, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSplitter,
    QSpinBox,
    QWidget,
)

from src.app.config_loader import AppConfig
from src.app.map_bridge import MapBridge
from src.agent.mango_assistant_service import MangoAssistantService
from src.models.map_payload import MapPayload
from src.models.route_request import RouteRequest
from src.models.route_result import RouteResult
from src.services.place_suggestion_service import PlaceSuggestionService, SuggestionItem
from src.services.route_planning_service import RoutePlanningService


LOGGER = logging.getLogger(__name__)
TWidget = TypeVar("TWidget", bound=QWidget)


# 汇总地点联想后台任务回主线程所需的信号。
class _SuggestionSignalBus(QObject):
    suggestionsReady = Signal(str, int, object)


# 汇总路径规划后台任务回主线程所需的信号。
class _RouteSignalBus(QObject):
    routeReady = Signal(int, object)


# 汇总聊天后台任务回主线程所需的信号。
class _ChatSignalBus(QObject):
    chatReady = Signal(int, object)


@dataclass(frozen=True, slots=True)
# 描述预设表单场景的输入参数组合。
class PresetProfile:
    name: str
    max_paths_per_strategy: int
    use_tmcs: bool
    densify_max_segment_m: float
    enable_divergence: bool
    anchor_ratio_1: float
    anchor_ratio_2: float
    offset_distance_1_m: float
    offset_distance_2_m: float
    description: str


# 主窗口控制器，负责协调界面、服务、地图和后台任务。
class MainWindowController:
    _SOURCE_SUFFIX_PATTERN = re.compile(r"\s*\[[^\]]+\]\s*$")
    _STRATEGY_SOURCE_AMAP = "高德策略"
    _STRATEGY_SOURCE_CUSTOM = "自研算法"
    _PRESET_QUICK = "快速"
    _PRESET_BALANCED = "平衡"
    _PRESET_FINE = "精细"
    _PRESET_PROFILES: dict[str, PresetProfile] = {
        _PRESET_QUICK: PresetProfile(
            name=_PRESET_QUICK,
            max_paths_per_strategy=1,
            use_tmcs=False,
            densify_max_segment_m=0.0,
            enable_divergence=False,
            anchor_ratio_1=0.35,
            anchor_ratio_2=0.65,
            offset_distance_1_m=300.0,
            offset_distance_2_m=600.0,
            description="优先速度：单策略单路径、关闭 TMCS 与折线加密，计算最快。",
        ),
        _PRESET_BALANCED: PresetProfile(
            name=_PRESET_BALANCED,
            max_paths_per_strategy=2,
            use_tmcs=True,
            densify_max_segment_m=80.0,
            enable_divergence=True,
            anchor_ratio_1=0.35,
            anchor_ratio_2=0.65,
            offset_distance_1_m=300.0,
            offset_distance_2_m=600.0,
            description="平衡方案：启用锚点发散，兼顾性能、稳定性和路线多样性。",
        ),
        _PRESET_FINE: PresetProfile(
            name=_PRESET_FINE,
            max_paths_per_strategy=3,
            use_tmcs=True,
            densify_max_segment_m=40.0,
            enable_divergence=True,
            anchor_ratio_1=0.30,
            anchor_ratio_2=0.70,
            offset_distance_1_m=300.0,
            offset_distance_2_m=600.0,
            description="精细优先：更高点密度+候选发散，适合对比实验与细粒度分析。",
        ),
    }

    # 初始化主窗口控制器并准备服务、线程池和本地缓存目录。
    def __init__(
        self,
        project_root: Path,
        app_config: AppConfig,
        route_service: RoutePlanningService,
        place_suggestion_service: PlaceSuggestionService | None = None,
        mango_assistant_service: MangoAssistantService | None = None,
    ) -> None:
        self.project_root = project_root
        self.app_config = app_config
        self.route_service = route_service
        self.place_suggestion_service = place_suggestion_service or PlaceSuggestionService(
            amap_key=app_config.amap.web_service_key or app_config.amap.js_key,
            request_timeout_s=app_config.amap.timeout_s,
            retry=app_config.amap.retry,
            cache_ttl_s=app_config.amap.cache_ttl_s,
        )
        self.mango_assistant_service = mango_assistant_service or MangoAssistantService(
            agent_name=self.app_config.assistant.name,
            api_key=self.app_config.assistant.api_key,
            endpoint=self.app_config.assistant.endpoint,
            model=self.app_config.assistant.model,
            timeout_s=self.app_config.assistant.timeout_s,
            retry=self.app_config.assistant.retry,
        )

        self.ui_path = self.project_root / self.app_config.ui.file
        self.map_html_path = self.project_root / self.app_config.ui.map_html
        self.result_detail_map_html_path = self.project_root / "ui" / "result_detail_map.html"
        self.settings_ui_path = self.project_root / "ui" / "设置界面.ui"
        self.result_stats_ui_path = self.project_root / "ui" / "结果统计界面.ui"
        self.result_detail_ui_path = self.project_root / "ui" / "结果详情界面.ui"
        self.ui_settings_path = self.project_root / "data" / "cache" / "ui_settings.json"
        self.result_history_path = self.project_root / "data" / "cache" / "result_history.json"
        self.result_detail_cache_dir = self.project_root / "data" / "cache" / "result_details"
        self.app_icon_path = self.project_root / "ui" / "assets" / "app_icon.svg"

        # 联想请求放到后台线程，避免输入卡顿。
        self._suggest_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="place_suggest")
        self._suggest_signal_bus = _SuggestionSignalBus()
        self._suggest_signal_bus.suggestionsReady.connect(self._on_suggestions_ready)
        self._suggest_seq: dict[str, int] = {"start": 0, "end": 0}
        self._pending_keyword: dict[str, str] = {"start": "", "end": ""}
        self._recent_locations: list[str] = []
        self._display_to_value: dict[str, dict[str, str]] = {"start": {}, "end": {}}
        # 路径规划放到后台线程，配合假的进度条避免界面阻塞。
        self._route_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="route_plan")
        self._route_signal_bus = _RouteSignalBus()
        self._route_signal_bus.routeReady.connect(self._on_route_ready)
        self._route_seq = 0
        self._route_running = False
        self._active_request: RouteRequest | None = None
        self._active_strategy_source = self._STRATEGY_SOURCE_AMAP
        self._progress_expected_s = 1.5
        self._progress_started_at = 0.0
        self._progress_last_value = 0
        self._chat_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mango_chat")
        self._chat_signal_bus = _ChatSignalBus()
        self._chat_signal_bus.chatReady.connect(self._on_chat_ready)
        self._chat_seq = 0
        self._chat_running = False
        self._chat_history: list[dict[str, str]] = []
        self._result_history: list[dict[str, object]] = self._load_result_history()

        self.window = self._load_ui(self.ui_path)
        self.map_ready = False
        self.pending_payload: MapPayload | None = None

        self.progress_timer = QTimer(self.window)
        self.progress_timer.setInterval(120)
        self.progress_timer.timeout.connect(self._tick_fake_progress)

        self._bind_widgets()
        self._setup_location_autocomplete()
        self._setup_map_bridge()
        self._init_ui_state()
        self._connect_signals()

    # 加载主窗口 UI 文件并返回窗口实例。
    def _load_ui(self, ui_path: Path) -> QMainWindow:
        if not ui_path.exists():
            raise FileNotFoundError(f"UI 文件不存在: {ui_path}")

        qfile = QFile(str(ui_path))
        if not qfile.open(QFile.ReadOnly):
            raise RuntimeError(f"UI 文件打开失败: {ui_path}")

        loader = QUiLoader()
        loader.registerCustomWidget(QWebEngineView)
        loaded = loader.load(qfile)
        qfile.close()

        if loaded is None or not isinstance(loaded, QMainWindow):
            raise RuntimeError("UI 根节点必须是 QMainWindow。")
        return loaded

    # 加载对话框 UI 文件并返回对话框实例。
    def _load_dialog_ui(self, ui_path: Path, description: str = "对话框") -> QDialog:
        if not ui_path.exists():
            raise FileNotFoundError(f"{description}文件不存在: {ui_path}")

        qfile = QFile(str(ui_path))
        if not qfile.open(QFile.ReadOnly):
            raise RuntimeError(f"{description}打开失败: {ui_path}")

        loader = QUiLoader()
        loader.registerCustomWidget(QWebEngineView)
        loaded = loader.load(qfile)
        qfile.close()

        if loaded is None or not isinstance(loaded, QDialog):
            raise RuntimeError(f"{description}根节点必须是 QDialog。")
        return loaded

    # 从指定父组件中查找并校验子控件类型。
    def _require_child_widget(
        self,
        parent: QWidget,
        name: str,
        widget_type: Type[TWidget],
    ) -> TWidget:
        widget = parent.findChild(widget_type, name)
        if widget is None:
            raise RuntimeError(f"设置界面缺少控件: {name}")
        return widget

    # 从主窗口中查找并校验控件类型。
    def _require_widget(self, name: str, widget_type: Type[TWidget]) -> TWidget:
        widget = self.window.findChild(widget_type, name)
        if widget is None:
            raise RuntimeError(f"缺少必要控件: {name}")
        return widget

    # 绑定界面中需要频繁访问的控件引用。
    def _bind_widgets(self) -> None:
        self.splitter_main = self._require_widget("splitterMain", QSplitter)
        self.line_edit_start = self._require_widget("lineEditStartPoint", QLineEdit)
        self.line_edit_end = self._require_widget("lineEditEndPoint", QLineEdit)
        self.combo_strategy_source = self._require_widget("comboBoxStrategySource", QComboBox)
        self.combo_algorithm = self._require_widget("comboBoxAlgorithmType", QComboBox)
        self.combo_fruit = self._require_widget("comboBoxFruitType", QComboBox)
        self.combo_transport = self._require_widget("comboBoxTransportMode", QComboBox)
        self.datetime_depart = self._require_widget("dateTimeEditDepartTime", QDateTimeEdit)
        self.spin_load = self._require_widget("doubleSpinBoxLoadWeight", QDoubleSpinBox)

        self.btn_settings = self._require_widget("pushButtonSettings", QPushButton)
        self.btn_run = self._require_widget("pushButtonRun", QPushButton)
        self.btn_reset = self._require_widget("pushButtonReset", QPushButton)
        self.btn_result_stats = self._require_widget("pushButtonResultStats", QPushButton)

        self.map_view = self._require_widget("webEngineViewChinaMap", QWebEngineView)
        self.progress_bar_compute = self._require_widget("progressBarCompute", QProgressBar)
        self.label_estimated_compute_value = self._require_widget(
            "labelEstimatedComputeValue",
            QLabel,
        )

        self.edit_eta = self._require_widget("lineEditEstimatedTravelTime", QLineEdit)
        self.edit_compute = self._require_widget("lineEditComputeElapsed", QLineEdit)
        self.edit_distance = self._require_widget("lineEditTotalDistance", QLineEdit)
        self.edit_cost = self._require_widget("lineEditEstimatedCost", QLineEdit)
        self.edit_nodes = self._require_widget("lineEditPathNodes", QLineEdit)
        self.edit_edges = self._require_widget("lineEditPathEdges", QLineEdit)
        self.edit_freshness = self._require_widget("lineEditFreshnessAtArrival", QLineEdit)
        self.edit_freshness_delta = self._require_widget("lineEditFreshnessDeltaTo100", QLineEdit)
        self.edit_status = self._require_widget("lineEditResultStatus", QLineEdit)
        self.run_log = self._require_widget("plainTextEditRunLog", QPlainTextEdit)
        self.mango_chat = self._require_widget("plainTextEditMangoChat", QPlainTextEdit)
        self.mango_input = self._require_widget("lineEditMangoInput", QLineEdit)
        self.btn_mango_send = self._require_widget("pushButtonMangoSend", QPushButton)
        self.btn_mango_clear = self._require_widget("pushButtonMangoClear", QPushButton)

    # 初始化起终点输入框的地点联想能力。
    def _setup_location_autocomplete(self) -> None:
        """为起点和终点输入框启用带来源标签的联想下拉。"""
        self.start_suggest_model = QStringListModel(self.window)
        self.end_suggest_model = QStringListModel(self.window)

        self.start_completer = QCompleter(self.start_suggest_model, self.window)
        self.end_completer = QCompleter(self.end_suggest_model, self.window)

        for completer in (self.start_completer, self.end_completer):
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setMaxVisibleItems(12)

        self.line_edit_start.setCompleter(self.start_completer)
        self.line_edit_end.setCompleter(self.end_completer)

        # 选择候选后只保留纯地点文本，不保留来源标签。
        self.start_completer.activated[str].connect(
            lambda text: self._on_completion_selected("start", text)
        )
        self.end_completer.activated[str].connect(
            lambda text: self._on_completion_selected("end", text)
        )

        self.start_suggest_timer = QTimer(self.window)
        self.end_suggest_timer = QTimer(self.window)
        for timer in (self.start_suggest_timer, self.end_suggest_timer):
            timer.setSingleShot(True)
            timer.setInterval(220)

        self.start_suggest_timer.timeout.connect(lambda: self._trigger_suggestion_fetch("start"))
        self.end_suggest_timer.timeout.connect(lambda: self._trigger_suggestion_fetch("end"))
        self.line_edit_start.textEdited.connect(
            lambda text: self._on_location_text_edited("start", text)
        )
        self.line_edit_end.textEdited.connect(
            lambda text: self._on_location_text_edited("end", text)
        )

    # 处理地点输入变化并触发联想查询流程。
    def _on_location_text_edited(self, field: str, text: str) -> None:
        keyword = text.strip()
        self._pending_keyword[field] = keyword
        if not keyword:
            self._set_suggestion_list(field, [], show_popup=False)
            return

        timer = self.start_suggest_timer if field == "start" else self.end_suggest_timer
        timer.start()

    # 在后台线程中发起地点联想请求。
    def _trigger_suggestion_fetch(self, field: str) -> None:
        keyword = self._pending_keyword.get(field, "").strip()
        if not keyword:
            return

        self._suggest_seq[field] += 1
        current_seq = self._suggest_seq[field]

        # 先展示最近输入命中的候选，减少等待感。
        quick = self._suggest_from_recent(keyword, limit=6)
        if quick:
            self._set_suggestion_list(field, quick, show_popup=True)

        future = self._suggest_executor.submit(
            self.place_suggestion_service.suggest_with_source,
            keyword,
            12,
        )
        future.add_done_callback(
            lambda fut, f=field, seq=current_seq, kw=keyword: self._on_suggestion_future_done(
                f, seq, kw, fut
            )
        )

    # 接收联想任务完成回调并转发到主线程处理。
    def _on_suggestion_future_done(
        self,
        field: str,
        seq: int,
        keyword: str,
        future: Future[list[SuggestionItem]],
    ) -> None:
        try:
            from_service = future.result()
        except Exception as exc:  # pragma: no cover
            LOGGER.debug("地点联想查询失败: %s", exc)
            from_service = []

        merged = self._merge_suggestions(keyword, from_service, limit=12)
        payload = [(item.text, item.source) for item in merged]
        self._suggest_signal_bus.suggestionsReady.emit(field, seq, payload)

    # 在主线程刷新联想候选列表。
    def _on_suggestions_ready(self, field: str, seq: int, suggestions_obj: object) -> None:
        if seq != self._suggest_seq.get(field, -1):
            return

        suggestions: list[SuggestionItem] = []
        if isinstance(suggestions_obj, list):
            for row in suggestions_obj:
                if not isinstance(row, tuple) or len(row) != 2:
                    continue
                text, source = row
                text_str = str(text).strip()
                source_str = str(source).strip()
                if not text_str:
                    continue
                suggestions.append(
                    SuggestionItem(
                        text=text_str,
                        source=source_str or PlaceSuggestionService.SOURCE_HISTORY,
                    )
                )

        self._set_suggestion_list(field, suggestions, show_popup=True)

    # 处理联想候选被选中后的输入回填逻辑。
    def _on_completion_selected(self, field: str, display_text: str) -> None:
        mapping = self._display_to_value.get(field, {})
        value = mapping.get(display_text, display_text)
        line_edit = self.line_edit_start if field == "start" else self.line_edit_end
        line_edit.setText(value)
        line_edit.setCursorPosition(len(value))

    # 刷新指定输入框的联想候选列表内容。
    def _set_suggestion_list(
        self,
        field: str,
        suggestions: list[SuggestionItem],
        show_popup: bool,
    ) -> None:
        line_edit = self.line_edit_start if field == "start" else self.line_edit_end
        model = self.start_suggest_model if field == "start" else self.end_suggest_model
        completer = self.start_completer if field == "start" else self.end_completer

        display_list: list[str] = []
        mapping: dict[str, str] = {}
        for item in suggestions:
            text = item.text.strip()
            source = item.source.strip() or PlaceSuggestionService.SOURCE_HISTORY
            if not text:
                continue
            display = f"{text}  [{source}]"
            if display in mapping:
                continue
            mapping[display] = text
            display_list.append(display)

        self._display_to_value[field] = mapping
        model.setStringList(display_list)

        if show_popup and display_list and line_edit.hasFocus() and line_edit.text().strip():
            completer.complete()

    # 从最近使用地点中筛选本地联想结果。
    def _suggest_from_recent(self, keyword: str, limit: int) -> list[SuggestionItem]:
        kw = keyword.lower()
        prefix = [x for x in self._recent_locations if x.lower().startswith(kw)]
        contain = [x for x in self._recent_locations if kw in x.lower() and x not in prefix]
        merged = (prefix + contain)[:limit]
        return [
            SuggestionItem(text=item, source=PlaceSuggestionService.SOURCE_HISTORY)
            for item in merged
        ]

    # 合并本地与远程联想结果并完成去重排序。
    def _merge_suggestions(
        self,
        keyword: str,
        from_service: list[SuggestionItem],
        limit: int,
    ) -> list[SuggestionItem]:
        merged: list[SuggestionItem] = []
        seen: set[str] = set()

        for item in self._suggest_from_recent(keyword, limit):
            if item.text in seen:
                continue
            seen.add(item.text)
            merged.append(item)

        for item in from_service:
            text = item.text.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(
                SuggestionItem(
                    text=text,
                    source=item.source.strip() or PlaceSuggestionService.SOURCE_HISTORY,
                )
            )
            if len(merged) >= limit:
                break
        return merged

    # 记录最近使用的地点文本。
    def _remember_location(self, value: str) -> None:
        text = value.strip()
        if not text:
            return
        if text in self._recent_locations:
            self._recent_locations.remove(text)
        self._recent_locations.insert(0, text)
        if len(self._recent_locations) > 80:
            self._recent_locations = self._recent_locations[:80]

    # 规范化地点文本以便比较和缓存。
    def _normalize_place_text(self, value: str) -> str:
        """清理输入末尾的来源标签，例如“北京南站 [高德]”。"""
        text = value.strip()
        text = self._SOURCE_SUFFIX_PATTERN.sub("", text).strip()
        return text

    # 初始化地图页面与 Qt 之间的桥接对象。
    def _setup_map_bridge(self) -> None:
        self._configure_map_view(self.map_view)

        self.map_bridge = MapBridge()
        self.map_channel = QWebChannel(self.map_view.page())
        self.map_channel.registerObject("pyBridge", self.map_bridge)
        self.map_view.page().setWebChannel(self.map_channel)

        self.map_view.loadFinished.connect(self._on_map_load_finished)
        self.map_bridge.mapReady.connect(self._on_map_ready)
        self.map_bridge.jsLog.connect(self._on_js_log)

    # 配置地图 Web 视图的基础行为。
    def _configure_map_view(self, map_view: QWebEngineView) -> None:
        # 允许本地 HTML（setHtml）加载远程 JS/CSS 资源，否则高德 loader.js 无法加载。
        settings = map_view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)

    # 初始化界面默认状态和初始展示内容。
    def _init_ui_state(self) -> None:
        self._apply_window_icon()
        self._restore_persisted_settings()
        self._init_strategy_options()
        self.datetime_depart.setDateTime(QDateTime.currentDateTime())
        self._clear_outputs()
        self._reset_progress_display()
        self._apply_main_layout_ratio()
        self._load_map_html()
        self._init_mango_assistant()
        self._append_log("界面已启动，等待输入。")

    # 初始化芒小果助手区域的默认提示与状态。
    def _init_mango_assistant(self) -> None:
        self.mango_chat.clear()
        self._chat_history.clear()
        season = self._season_name(datetime.now().month)
        self._append_mango_line(
            "芒小果",
            (
                f"你好，我是芒小果。现在是{season}，你可以问我："
                "“这个季节推荐吃什么水果？”或“荔枝和葡萄哪个更适合现在吃？”"
            ),
        )

    # 为主窗口和相关对话框应用统一图标。
    def _apply_window_icon(self) -> None:
        """为主窗口设置项目图标。"""
        if not self.app_icon_path.exists():
            return
        icon = QIcon(str(self.app_icon_path))
        if icon.isNull():
            return
        self.window.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    # 恢复本地持久化的候选参数和界面设置。
    def _restore_persisted_settings(self) -> None:
        """启动时恢复本地保存的设置参数。"""
        payload = self._load_persisted_settings_payload()
        if not payload:
            return

        options = payload.get("candidate_options")
        if not isinstance(options, dict):
            return

        restored = self._coerce_candidate_options(options)
        self._apply_runtime_candidate_settings(
            max_paths_per_strategy=restored["max_paths_per_strategy"],
            use_tmcs=restored["use_tmcs"],
            densify_max_segment_m=restored["densify_max_segment_m"],
            enable_divergence=restored["enable_divergence"],
            anchor_ratio_1=restored["anchor_ratio_1"],
            anchor_ratio_2=restored["anchor_ratio_2"],
            offset_distance_1_m=restored["offset_distance_1_m"],
            offset_distance_2_m=restored["offset_distance_2_m"],
            source_tag="启动恢复",
            persist=False,
            show_feedback=False,
        )
        self._append_log("已恢复上次设置参数。")

    # 读取持久化设置文件内容。
    def _load_persisted_settings_payload(self) -> dict[str, object]:
        if not self.ui_settings_path.exists():
            return {}
        try:
            payload = json.loads(self.ui_settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("读取本地设置失败: %s", exc)
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    # 校正并补全持久化的候选参数配置。
    def _coerce_candidate_options(self, raw: dict[str, object]) -> dict[str, float | int | bool]:
        current = self.route_service.get_custom_candidate_options()
        current_anchor = current.get("divergence_anchor_ratios", [0.35, 0.65])
        if not isinstance(current_anchor, list) or len(current_anchor) < 2:
            current_anchor = [0.35, 0.65]
        current_offset = current.get("divergence_offsets_m", [300.0, 600.0])
        if not isinstance(current_offset, list) or len(current_offset) < 2:
            current_offset = [300.0, 600.0]

        def _to_int(value: object, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _to_float(value: object, default: float) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        return {
            "max_paths_per_strategy": _to_int(
                raw.get("max_paths_per_strategy"),
                int(current.get("max_paths_per_strategy", 2)),
            ),
            "use_tmcs": bool(raw.get("use_tmcs", bool(current.get("use_tmcs", True)))),
            "densify_max_segment_m": _to_float(
                raw.get("densify_max_segment_m"),
                float(current.get("densify_max_segment_m", 80.0)),
            ),
            "enable_divergence": bool(raw.get("enable_divergence", bool(current.get("enable_divergence", False)))),
            "anchor_ratio_1": _to_float(raw.get("anchor_ratio_1"), float(current_anchor[0])),
            "anchor_ratio_2": _to_float(raw.get("anchor_ratio_2"), float(current_anchor[1])),
            "offset_distance_1_m": _to_float(raw.get("offset_distance_1_m"), float(current_offset[0])),
            "offset_distance_2_m": _to_float(raw.get("offset_distance_2_m"), float(current_offset[1])),
        }

    # 保存当前界面设置和候选参数到本地文件。
    def _save_persisted_settings(self) -> None:
        options = self.route_service.get_custom_candidate_options()
        anchor_ratios = options.get("divergence_anchor_ratios", [0.35, 0.65])
        if not isinstance(anchor_ratios, list) or len(anchor_ratios) < 2:
            anchor_ratios = [0.35, 0.65]
        offsets = options.get("divergence_offsets_m", [300.0, 600.0])
        if not isinstance(offsets, list) or len(offsets) < 2:
            offsets = [300.0, 600.0]

        payload = {
            "candidate_options": {
                "max_paths_per_strategy": int(options.get("max_paths_per_strategy", 2)),
                "use_tmcs": bool(options.get("use_tmcs", True)),
                "densify_max_segment_m": float(options.get("densify_max_segment_m", 80.0)),
                "enable_divergence": bool(options.get("enable_divergence", False)),
                "anchor_ratio_1": float(anchor_ratios[0]),
                "anchor_ratio_2": float(anchor_ratios[1]),
                "offset_distance_1_m": float(offsets[0]),
                "offset_distance_2_m": float(offsets[1]),
            }
        }
        try:
            self.ui_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.ui_settings_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            LOGGER.warning("保存本地设置失败: %s", exc)

    # 调整主界面三栏布局比例。
    def _apply_main_layout_ratio(self) -> None:
        """统一三栏布局比例，保证地图区优先展示。"""
        self.splitter_main.setStretchFactor(0, 26)
        self.splitter_main.setStretchFactor(1, 48)
        self.splitter_main.setStretchFactor(2, 26)
        self.splitter_main.setSizes([360, 860, 400])

    # 初始化策略来源和算法下拉框选项。
    def _init_strategy_options(self) -> None:
        """初始化二级策略选择：先选来源，再选具体策略。"""
        self.combo_strategy_source.blockSignals(True)
        self.combo_strategy_source.clear()
        self.combo_strategy_source.addItem(self._STRATEGY_SOURCE_AMAP)
        self.combo_strategy_source.addItem(self._STRATEGY_SOURCE_CUSTOM)

        default_strategy = self.app_config.routing.default_strategy.strip()
        custom_names = list((self.app_config.routing.custom_algorithm_map or {}).keys())
        default_source = (
            self._STRATEGY_SOURCE_CUSTOM
            if default_strategy in custom_names
            else self._STRATEGY_SOURCE_AMAP
        )
        source_index = self.combo_strategy_source.findText(default_source)
        self.combo_strategy_source.setCurrentIndex(source_index if source_index >= 0 else 0)
        self.combo_strategy_source.blockSignals(False)

        self._reload_strategy_options(default_strategy)

    # 按当前策略来源重载可选算法列表。
    def _reload_strategy_options(self, preferred: str = "") -> None:
        """按策略来源刷新“具体策略”下拉。"""
        source = self.combo_strategy_source.currentText().strip()
        if source == self._STRATEGY_SOURCE_CUSTOM:
            names = list((self.app_config.routing.custom_algorithm_map or {}).keys())
        else:
            names = list((self.app_config.routing.amap_strategy_map or {}).keys())

        self.combo_algorithm.clear()
        for name in names:
            self.combo_algorithm.addItem(name)

        if self.combo_algorithm.count() <= 0:
            self.combo_algorithm.setCurrentIndex(-1)
            return

        target = self.combo_algorithm.findText(preferred.strip())
        if target >= 0:
            self.combo_algorithm.setCurrentIndex(target)
        else:
            self.combo_algorithm.setCurrentIndex(0)

    # 响应策略来源切换并刷新算法选项。
    def _on_strategy_source_changed(self, source_text: str) -> None:
        """策略来源切换后联动刷新具体策略。"""
        self._reload_strategy_options()
        show_text = source_text.strip() or self._STRATEGY_SOURCE_AMAP
        self._append_log(f"已切换策略来源: {show_text}")

    # 将设置对话框中的候选参数应用到业务服务。
    def _apply_runtime_candidate_settings(
        self,
        max_paths_per_strategy: int,
        use_tmcs: bool,
        densify_max_segment_m: float,
        enable_divergence: bool,
        anchor_ratio_1: float,
        anchor_ratio_2: float,
        offset_distance_1_m: float,
        offset_distance_2_m: float,
        source_tag: str,
        persist: bool = True,
        show_feedback: bool = True,
    ) -> None:
        self.route_service.set_custom_candidate_options(
            max_paths_per_strategy=max_paths_per_strategy,
            use_tmcs=use_tmcs,
            densify_max_segment_m=densify_max_segment_m,
            enable_divergence=enable_divergence,
            divergence_anchor_ratios=[anchor_ratio_1, anchor_ratio_2],
            divergence_offsets_m=[offset_distance_1_m, offset_distance_2_m],
        )
        self.app_config.routing.custom_candidate_max_paths_per_strategy = int(
            self.route_service.custom_candidate_max_paths_per_strategy
        )
        self.app_config.routing.custom_candidate_use_tmcs = bool(self.route_service.custom_candidate_use_tmcs)
        self.app_config.routing.custom_candidate_densify_max_segment_m = float(
            self.route_service.custom_candidate_densify_max_segment_m
        )
        self.app_config.routing.custom_candidate_enable_divergence = bool(
            self.route_service.custom_candidate_enable_divergence
        )
        self.app_config.routing.custom_candidate_divergence_anchor_ratios = list(
            self.route_service.custom_candidate_divergence_anchor_ratios
        )
        self.app_config.routing.custom_candidate_divergence_offsets_m = list(
            self.route_service.custom_candidate_divergence_offsets_m
        )

        if persist:
            self._save_persisted_settings()

        if show_feedback:
            self.window.statusBar().showMessage("设置已应用")
            self._append_log(
                f"设置已应用({source_tag}): 每策略候选={self.route_service.custom_candidate_max_paths_per_strategy}, "
                f"TMCS={'开' if self.route_service.custom_candidate_use_tmcs else '关'}, "
                f"加密阈值={self.route_service.custom_candidate_densify_max_segment_m:.1f}m, "
                f"候选发散={'开' if self.route_service.custom_candidate_enable_divergence else '关'}, "
                f"锚点={self.route_service.custom_candidate_divergence_anchor_ratios}, "
                f"偏移={self.route_service.custom_candidate_divergence_offsets_m}"
            )

    # 打开设置对话框并处理候选参数应用。
    def on_settings_clicked(self) -> None:
        dialog = self._load_dialog_ui(self.settings_ui_path, "设置界面")
        dialog.setWindowModality(Qt.WindowModal)

        radio_fast = self._require_child_widget(dialog, "radioButtonPresetFast", QRadioButton)
        radio_balanced = self._require_child_widget(dialog, "radioButtonPresetBalanced", QRadioButton)
        radio_fine = self._require_child_widget(dialog, "radioButtonPresetFine", QRadioButton)
        spin_paths = self._require_child_widget(dialog, "spinBoxMaxPathsPerStrategy", QSpinBox)
        check_tmcs = self._require_child_widget(dialog, "checkBoxUseTmcs", QCheckBox)
        spin_densify = self._require_child_widget(
            dialog,
            "doubleSpinBoxDensifyMaxSegmentM",
            QDoubleSpinBox,
        )
        check_divergence = self._require_child_widget(dialog, "checkBoxEnableDivergence", QCheckBox)
        spin_anchor_1 = self._require_child_widget(dialog, "doubleSpinBoxAnchorRatio1Pct", QDoubleSpinBox)
        spin_anchor_2 = self._require_child_widget(dialog, "doubleSpinBoxAnchorRatio2Pct", QDoubleSpinBox)
        spin_offset_1 = self._require_child_widget(dialog, "doubleSpinBoxOffsetDistance1M", QDoubleSpinBox)
        spin_offset_2 = self._require_child_widget(dialog, "doubleSpinBoxOffsetDistance2M", QDoubleSpinBox)
        label_hint = self._require_child_widget(dialog, "labelPresetHint", QLabel)
        btn_apply = self._require_child_widget(dialog, "pushButtonApplySettings", QPushButton)
        btn_ok = self._require_child_widget(dialog, "pushButtonOkSettings", QPushButton)
        btn_cancel = self._require_child_widget(dialog, "pushButtonCancelSettings", QPushButton)

        current_options = self.route_service.get_custom_candidate_options()
        spin_paths.setValue(int(current_options["max_paths_per_strategy"]))
        spin_densify.setValue(float(current_options["densify_max_segment_m"]))
        check_tmcs.setChecked(bool(current_options["use_tmcs"]))
        check_divergence.setChecked(bool(current_options["enable_divergence"]))

        anchor_ratios = current_options.get("divergence_anchor_ratios", [0.35, 0.65])
        if not isinstance(anchor_ratios, list):
            anchor_ratios = [0.35, 0.65]
        if len(anchor_ratios) < 2:
            anchor_ratios = [0.35, 0.65]
        spin_anchor_1.setValue(float(anchor_ratios[0]) * 100.0)
        spin_anchor_2.setValue(float(anchor_ratios[1]) * 100.0)

        offset_distances = current_options.get("divergence_offsets_m", [300.0, 600.0])
        if not isinstance(offset_distances, list):
            offset_distances = [300.0, 600.0]
        if len(offset_distances) < 2:
            offset_distances = [300.0, 600.0]
        spin_offset_1.setValue(float(offset_distances[0]))
        spin_offset_2.setValue(float(offset_distances[1]))

        def set_preset_to_controls(preset_name: str) -> None:
            profile = self._PRESET_PROFILES[preset_name]
            spin_paths.setValue(profile.max_paths_per_strategy)
            check_tmcs.setChecked(profile.use_tmcs)
            spin_densify.setValue(profile.densify_max_segment_m)
            check_divergence.setChecked(profile.enable_divergence)
            spin_anchor_1.setValue(profile.anchor_ratio_1 * 100.0)
            spin_anchor_2.setValue(profile.anchor_ratio_2 * 100.0)
            spin_offset_1.setValue(profile.offset_distance_1_m)
            spin_offset_2.setValue(profile.offset_distance_2_m)
            label_hint.setText(f"当前预设：{profile.description}")

        def find_exact_preset_name() -> str | None:
            for preset_name, profile in self._PRESET_PROFILES.items():
                if (
                    spin_paths.value() == profile.max_paths_per_strategy
                    and check_tmcs.isChecked() == profile.use_tmcs
                    and round(spin_densify.value(), 1) == round(profile.densify_max_segment_m, 1)
                    and check_divergence.isChecked() == profile.enable_divergence
                    and round(spin_anchor_1.value() / 100.0, 3) == round(profile.anchor_ratio_1, 3)
                    and round(spin_anchor_2.value() / 100.0, 3) == round(profile.anchor_ratio_2, 3)
                    and round(spin_offset_1.value(), 1) == round(profile.offset_distance_1_m, 1)
                    and round(spin_offset_2.value(), 1) == round(profile.offset_distance_2_m, 1)
                ):
                    return preset_name
            return None

        def refresh_hint_from_controls() -> None:
            preset_name = find_exact_preset_name()
            if preset_name:
                label_hint.setText(f"当前预设：{self._PRESET_PROFILES[preset_name].description}")
            else:
                label_hint.setText("当前为自定义参数组合：可继续微调，或点击预设快速切换。")

        def refresh_divergence_controls() -> None:
            enabled = check_divergence.isChecked()
            spin_anchor_1.setEnabled(enabled)
            spin_anchor_2.setEnabled(enabled)
            spin_offset_1.setEnabled(enabled)
            spin_offset_2.setEnabled(enabled)

        detected_preset_name = find_exact_preset_name() or self._PRESET_BALANCED
        if detected_preset_name == self._PRESET_QUICK:
            radio_fast.setChecked(True)
        elif detected_preset_name == self._PRESET_FINE:
            radio_fine.setChecked(True)
        else:
            radio_balanced.setChecked(True)
        refresh_divergence_controls()
        refresh_hint_from_controls()

        radio_fast.toggled.connect(lambda checked: checked and set_preset_to_controls(self._PRESET_QUICK))
        radio_balanced.toggled.connect(
            lambda checked: checked and set_preset_to_controls(self._PRESET_BALANCED)
        )
        radio_fine.toggled.connect(lambda checked: checked and set_preset_to_controls(self._PRESET_FINE))
        spin_paths.valueChanged.connect(lambda _: refresh_hint_from_controls())
        check_tmcs.toggled.connect(lambda _: refresh_hint_from_controls())
        spin_densify.valueChanged.connect(lambda _: refresh_hint_from_controls())
        check_divergence.toggled.connect(lambda _: refresh_hint_from_controls())
        check_divergence.toggled.connect(lambda _: refresh_divergence_controls())
        spin_anchor_1.valueChanged.connect(lambda _: refresh_hint_from_controls())
        spin_anchor_2.valueChanged.connect(lambda _: refresh_hint_from_controls())
        spin_offset_1.valueChanged.connect(lambda _: refresh_hint_from_controls())
        spin_offset_2.valueChanged.connect(lambda _: refresh_hint_from_controls())

        def apply_settings(source_tag: str) -> None:
            self._apply_runtime_candidate_settings(
                max_paths_per_strategy=spin_paths.value(),
                use_tmcs=check_tmcs.isChecked(),
                densify_max_segment_m=float(spin_densify.value()),
                enable_divergence=check_divergence.isChecked(),
                anchor_ratio_1=float(spin_anchor_1.value() / 100.0),
                anchor_ratio_2=float(spin_anchor_2.value() / 100.0),
                offset_distance_1_m=float(spin_offset_1.value()),
                offset_distance_2_m=float(spin_offset_2.value()),
                source_tag=source_tag,
            )

        def on_ok_clicked() -> None:
            apply_settings("确定")
            dialog.accept()

        btn_apply.clicked.connect(lambda: apply_settings("应用"))
        btn_ok.clicked.connect(on_ok_clicked)
        btn_cancel.clicked.connect(dialog.reject)

        dialog.exec()

    # 打开结果统计对话框并刷新历史记录。
    def on_result_stats_clicked(self) -> None:
        dialog = self._load_dialog_ui(self.result_stats_ui_path, "结果统计界面")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        dialog.showMaximized()

        summary_labels = {
            "total": self._require_child_widget(dialog, "labelSummaryTotalValue", QLabel),
            "success": self._require_child_widget(dialog, "labelSummarySuccessValue", QLabel),
            "error": self._require_child_widget(dialog, "labelSummaryErrorValue", QLabel),
            "avg_compute": self._require_child_widget(dialog, "labelSummaryAvgComputeValue", QLabel),
            "avg_distance": self._require_child_widget(dialog, "labelSummaryAvgDistanceValue", QLabel),
            "best_delta": self._require_child_widget(dialog, "labelSummaryBestFreshnessValue", QLabel),
        }
        hint_label = self._require_child_widget(dialog, "labelResultStatsHint", QLabel)
        table = self._require_child_widget(dialog, "tableWidgetResultStats", QTableWidget)
        btn_clear = self._require_child_widget(dialog, "pushButtonClearResultStats", QPushButton)
        btn_close = self._require_child_widget(dialog, "pushButtonCloseResultStats", QPushButton)

        hint_label.setText(
            f"{hint_label.text().strip()} 双击已保存图结构的自研算法记录，可查看路线和图结构。"
        )
        self._setup_result_stats_table(table)
        self._refresh_result_stats_dialog(summary_labels, table)

        def on_clear_clicked() -> None:
            if not self._result_history:
                QMessageBox.information(dialog, "暂无数据", "当前还没有可清空的结果统计记录。")
                return
            reply = QMessageBox.question(
                dialog,
                "清空统计",
                "确定要清空当前保存的所有结果统计记录吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            self._result_history.clear()
            self._save_result_history()
            self._refresh_result_stats_dialog(summary_labels, table)

        btn_clear.clicked.connect(on_clear_clicked)
        btn_close.clicked.connect(dialog.accept)
        table.cellDoubleClicked.connect(
            lambda row, _column: self._on_result_stats_row_double_clicked(dialog, table, row)
        )
        dialog.exec()

    # 初始化结果统计表格的列定义和交互行为。
    def _setup_result_stats_table(self, table: QTableWidget) -> None:
        table.setColumnCount(14)
        table.setHorizontalHeaderLabels(
            [
                "时间",
                "起点",
                "终点",
                "来源",
                "策略",
                "水果",
                "运输",
                "状态",
                "计算用时(ms)",
                "总里程(km)",
                "所需时间(h)",
                "到达保鲜度",
                "距100偏差",
                "结果信息",
            ]
        )
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        for column in (1, 2, 4, 13):
            header.setSectionResizeMode(column, QHeaderView.Stretch)

    # 刷新结果统计对话框中的历史数据与筛选显示。
    def _refresh_result_stats_dialog(
        self,
        summary_labels: dict[str, QLabel],
        table: QTableWidget,
    ) -> None:
        total_count = len(self._result_history)
        success_rows = [entry for entry in self._result_history if str(entry.get("status", "")).lower() == "ok"]
        error_count = total_count - len(success_rows)
        avg_compute_ms = (
            sum(float(entry.get("compute_ms", 0.0)) for entry in self._result_history) / total_count
            if total_count
            else None
        )
        avg_distance_km = (
            sum(float(entry.get("total_distance_km", 0.0)) for entry in success_rows) / len(success_rows)
            if success_rows
            else None
        )
        valid_deltas = [
            float(entry.get("freshness_delta_to_100"))
            for entry in success_rows
            if entry.get("freshness_delta_to_100") is not None
        ]
        best_delta = min(valid_deltas) if valid_deltas else None

        summary_labels["total"].setText(str(total_count))
        summary_labels["success"].setText(str(len(success_rows)))
        summary_labels["error"].setText(str(error_count))
        summary_labels["avg_compute"].setText(
            "-" if avg_compute_ms is None else f"{avg_compute_ms:.1f} ms"
        )
        summary_labels["avg_distance"].setText(
            "-" if avg_distance_km is None else f"{avg_distance_km:.2f} km"
        )
        summary_labels["best_delta"].setText(
            "-" if best_delta is None else f"{best_delta:.2f}"
        )

        columns = [
            "timestamp",
            "start_text",
            "end_text",
            "strategy_source",
            "algorithm",
            "fruit_type",
            "transport_mode",
            "status",
            "compute_ms_text",
            "total_distance_text",
            "total_time_text",
            "freshness_text",
            "freshness_delta_text",
            "message",
        ]
        history_entries = list(reversed(self._result_history))
        table.setRowCount(len(history_entries))
        for row_index, entry in enumerate(history_entries):
            history_index = len(self._result_history) - 1 - row_index
            view = self._build_result_history_view(entry)
            for column_index, key in enumerate(columns):
                item = QTableWidgetItem(view[key])
                item.setData(Qt.UserRole, history_index)
                if column_index in {8, 9, 10, 11, 12}:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row_index, column_index, item)
        if not self._result_history:
            table.clearContents()
            table.setRowCount(0)

    # 处理结果统计表格双击事件并打开详情。
    def _on_result_stats_row_double_clicked(
        self,
        parent_dialog: QDialog,
        table: QTableWidget,
        row: int,
    ) -> None:
        entry = self._history_entry_from_table_row(table, row)
        if entry is None:
            return

        if str(entry.get("strategy_source", "")).strip() != self._STRATEGY_SOURCE_CUSTOM:
            QMessageBox.information(
                parent_dialog,
                "仅支持自研算法",
                "当前仅支持查看“自研算法”结果的路线和图结构详情。",
            )
            return

        if str(entry.get("status", "")).strip().lower() != "ok":
            QMessageBox.information(
                parent_dialog,
                "结果不可查看",
                "该条记录不是成功结果，暂无可展示的路线和图结构。",
            )
            return

        debug_payload = self._load_result_detail_payload(entry)
        if not isinstance(debug_payload, dict):
            QMessageBox.information(
                parent_dialog,
                "缺少详情",
                "这条历史记录没有保存图结构详情，可能是旧记录或当前算法未生成候选图，请重新运行支持图结构记录的自研算法后再查看。",
            )
            return

        self._open_result_detail_dialog(parent_dialog, entry, debug_payload)

    # 根据表格行恢复对应的历史记录条目。
    def _history_entry_from_table_row(
        self,
        table: QTableWidget,
        row: int,
    ) -> dict[str, object] | None:
        item = table.item(row, 0)
        if item is None:
            return None
        history_index = item.data(Qt.UserRole)
        if not isinstance(history_index, int):
            return None
        if history_index < 0 or history_index >= len(self._result_history):
            return None
        return self._result_history[history_index]

    # 打开单条路径结果的详情对话框。
    def _open_result_detail_dialog(
        self,
        parent_dialog: QDialog,
        entry: dict[str, object],
        debug_payload: dict[str, object],
    ) -> None:
        dialog = self._load_dialog_ui(self.result_detail_ui_path, "结果详情界面")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        dialog.resize(1380, 860)
        dialog.setMinimumSize(1100, 720)

        title_label = self._require_child_widget(dialog, "labelResultDetailTitle", QLabel)
        hint_label = self._require_child_widget(dialog, "labelResultDetailHint", QLabel)
        map_view = self._require_child_widget(dialog, "webEngineViewResultGraph", QWebEngineView)
        close_button = self._require_child_widget(dialog, "pushButtonCloseResultDetail", QPushButton)

        root_layout = dialog.layout()
        if root_layout is not None:
            root_layout.setStretch(0, 0)
            root_layout.setStretch(1, 0)
            root_layout.setStretch(2, 1)
            root_layout.setStretch(3, 0)

        title_label.setText("自研算法结果详情")
        node_count = int(debug_payload.get("node_count", 0))
        edge_count = int(debug_payload.get("edge_count", 0))
        hint_label.setText(
            f"当前记录：{str(entry.get('algorithm', '-'))}，"
            f"图规模为 {node_count} 个节点 / {edge_count} 条边。"
        )
        self._setup_result_detail_map(dialog, map_view, entry, debug_payload)

        close_button.clicked.connect(dialog.accept)
        dialog.exec()

    # 初始化结果详情页中的地图展示。
    def _setup_result_detail_map(
        self,
        dialog: QDialog,
        map_view: QWebEngineView,
        entry: dict[str, object],
        debug_payload: dict[str, object],
    ) -> None:
        self._configure_map_view(map_view)

        detail_bridge = MapBridge()
        detail_channel = QWebChannel(map_view.page())
        detail_channel.registerObject("pyBridge", detail_bridge)
        map_view.page().setWebChannel(detail_channel)

        dialog._detail_map_bridge = detail_bridge
        dialog._detail_map_channel = detail_channel

        payload = self._build_result_detail_map_payload(entry, debug_payload)

        def on_map_ready() -> None:
            detail_bridge.send_payload(payload)

        def on_map_log(message: str) -> None:
            LOGGER.info("结果详情地图: %s", message)

        def on_load_finished(ok: bool) -> None:
            if not ok:
                LOGGER.warning("结果详情地图页面加载失败。")

        map_view.loadFinished.connect(on_load_finished)
        detail_bridge.mapReady.connect(on_map_ready)
        detail_bridge.jsLog.connect(on_map_log)
        self._set_map_html_to_view(
            map_view,
            self._build_map_html_content(self.result_detail_map_html_path),
            self.result_detail_map_html_path,
        )

    # 构建结果详情页地图所需的数据载荷。
    def _build_result_detail_map_payload(
        self,
        entry: dict[str, object],
        debug_payload: dict[str, object],
    ) -> dict[str, object]:
        route_data = debug_payload.get("route")
        overlay_data = debug_payload.get("map_overlay")
        route_points = route_data.get("points_gcj02", []) if isinstance(route_data, dict) else []
        graph_edges = overlay_data.get("graph_edges", []) if isinstance(overlay_data, dict) else []
        return {
            "points": route_points,
            "graph_edges": graph_edges,
            "meta": {
                "start": str(entry.get("start_text", "-")),
                "end": str(entry.get("end_text", "-")),
                "algorithm": str(entry.get("algorithm", "-")),
                "strategy_source": str(entry.get("strategy_source", "-")),
                "node_count": int(debug_payload.get("node_count", 0)),
                "edge_count": int(debug_payload.get("edge_count", 0)),
            },
        }

    # 压缩调试载荷以减少持久化体积。
    def _compact_debug_payload(self, payload: dict[str, object]) -> dict[str, object]:
        route_data = payload.get("route")
        overlay_data = payload.get("map_overlay")
        route_points = route_data.get("points_gcj02", []) if isinstance(route_data, dict) else []
        graph_edges = overlay_data.get("graph_edges", []) if isinstance(overlay_data, dict) else []
        compact_edges: list[dict[str, object]] = []
        if isinstance(graph_edges, list):
            for edge in graph_edges:
                if not isinstance(edge, dict):
                    continue
                points = edge.get("points", [])
                if not isinstance(points, list) or len(points) < 2:
                    continue
                compact_points: list[list[float]] = []
                for point in points:
                    if not isinstance(point, list | tuple) or len(point) < 2:
                        continue
                    try:
                        compact_points.append([round(float(point[0]), 6), round(float(point[1]), 6)])
                    except (TypeError, ValueError):
                        continue
                if len(compact_points) < 2:
                    continue
                compact_edges.append(
                    {
                        "edge_id": int(edge.get("edge_id", 0)),
                        "road_class": str(edge.get("road_class", "")),
                        "is_route_edge": bool(edge.get("is_route_edge", False)),
                        "points": compact_points,
                    }
                )

        return {
            "detail_type": str(payload.get("detail_type", "custom_graph_route")),
            "strategy_name": str(payload.get("strategy_name", "")),
            "objective_label": str(payload.get("objective_label", "")),
            "is_time_dependent": bool(payload.get("is_time_dependent", False)),
            "node_count": int(payload.get("node_count", 0)),
            "edge_count": int(payload.get("edge_count", 0)),
            "route": {
                "points_gcj02": route_points if isinstance(route_points, list) else [],
            },
            "map_overlay": {
                "graph_edges": compact_edges,
            },
        }

    # 将结果详情载荷写入本地缓存文件。
    def _persist_result_detail_payload(
        self,
        entry: dict[str, object],
        debug_payload: dict[str, object],
    ) -> str | None:
        compact_payload = self._compact_debug_payload(debug_payload)
        timestamp_raw = str(entry.get("timestamp", "")).strip()
        timestamp_key = re.sub(r"[^0-9]", "", timestamp_raw) or "detail"
        algorithm_key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(entry.get("algorithm", "")).strip())
        algorithm_key = algorithm_key.strip("_") or "algorithm"
        file_path = self.result_detail_cache_dir / f"{timestamp_key}_{algorithm_key}.json"
        try:
            self.result_detail_cache_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("结果详情缓存保存失败: %s", exc)
            return None
        return str(file_path.relative_to(self.project_root))

    # 从缓存文件中读取结果详情载荷。
    def _load_result_detail_payload(self, entry: dict[str, object]) -> dict[str, object] | None:
        inline_payload = entry.get("debug_payload")
        if isinstance(inline_payload, dict):
            return self._compact_debug_payload(inline_payload)

        payload_path_raw = entry.get("debug_payload_path")
        if not isinstance(payload_path_raw, str) or not payload_path_raw.strip():
            return None
        payload_path = self.project_root / payload_path_raw
        if not payload_path.exists():
            return None
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("结果详情缓存读取失败: %s", exc)
            return None
        return dict(payload) if isinstance(payload, dict) else None
    # 将历史记录转换为表格展示用视图数据。
    def _build_result_history_view(self, entry: dict[str, object]) -> dict[str, str]:
        freshness_value = entry.get("freshness_at_arrival")
        delta_value = entry.get("freshness_delta_to_100")
        return {
            "timestamp": str(entry.get("timestamp", "-")),
            "start_text": str(entry.get("start_text", "-")),
            "end_text": str(entry.get("end_text", "-")),
            "strategy_source": str(entry.get("strategy_source", "-")),
            "algorithm": str(entry.get("algorithm", "-")),
            "fruit_type": str(entry.get("fruit_type", "-")),
            "transport_mode": str(entry.get("transport_mode", "-")),
            "status": str(entry.get("status", "-")),
            "compute_ms_text": f"{float(entry.get('compute_ms', 0.0)):.2f}",
            "total_distance_text": f"{float(entry.get('total_distance_km', 0.0)):.2f}",
            "total_time_text": f"{float(entry.get('total_time_h', 0.0)):.2f}",
            "freshness_text": "-" if freshness_value is None else f"{float(freshness_value):.2f}",
            "freshness_delta_text": "-" if delta_value is None else f"{float(delta_value):.2f}",
            "message": str(entry.get("message", "")),
        }

    # 读取本地保存的历史结果列表。
    def _load_result_history(self) -> list[dict[str, object]]:
        if not self.result_history_path.exists():
            return []

        try:
            payload = json.loads(self.result_history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("结果统计记录读取失败，回退为空: %s", exc)
            return []

        if not isinstance(payload, list):
            return []
        normalized: list[dict[str, object]] = []
        migrated = False
        for row in payload:
            if isinstance(row, dict):
                normalized_row = dict(row)
                inline_payload = normalized_row.get("debug_payload")
                if isinstance(inline_payload, dict):
                    payload_path = self._persist_result_detail_payload(normalized_row, inline_payload)
                    normalized_row["debug_payload_path"] = payload_path
                    normalized_row.pop("debug_payload", None)
                    migrated = True
                normalized.append(normalized_row)
        if migrated:
            self._result_history = normalized
            self._save_result_history()
        return normalized

    # 保存当前历史结果列表到本地文件。
    def _save_result_history(self) -> None:
        try:
            self.result_history_path.parent.mkdir(parents=True, exist_ok=True)
            self.result_history_path.write_text(
                json.dumps(self._result_history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("结果统计记录保存失败: %s", exc)

    # 连接界面控件、桥接对象和后台任务信号。
    def _connect_signals(self) -> None:
        self.combo_strategy_source.currentTextChanged.connect(self._on_strategy_source_changed)
        self.btn_settings.clicked.connect(self.on_settings_clicked)
        self.btn_run.clicked.connect(self.on_run_clicked)
        self.btn_reset.clicked.connect(self.on_reset_clicked)
        self.btn_result_stats.clicked.connect(self.on_result_stats_clicked)
        self.btn_mango_send.clicked.connect(self.on_mango_send_clicked)
        self.btn_mango_clear.clicked.connect(self.on_mango_clear_clicked)
        self.mango_input.returnPressed.connect(self.on_mango_send_clicked)

    # 向助手对话框追加一行聊天内容。
    def _append_mango_line(self, speaker: str, text: str) -> None:
        stamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.mango_chat.appendPlainText(f"[{stamp}] {speaker}: {text}")

    # 根据月份返回季节名称文案。
    def _season_name(self, month: int) -> str:
        if month in {3, 4, 5}:
            return "春季"
        if month in {6, 7, 8}:
            return "夏季"
        if month in {9, 10, 11}:
            return "秋季"
        return "冬季"

    # 发送用户消息并异步请求助手回复。
    def on_mango_send_clicked(self) -> None:
        if self._chat_running:
            QMessageBox.information(self.window, "芒小果忙碌中", "芒小果正在思考，请稍候。")
            return

        text = self.mango_input.text().strip()
        if not text:
            return

        self.mango_input.clear()
        self._append_mango_line("我", text)
        self._chat_history.append({"role": "user", "content": text})
        self._chat_history = self._chat_history[-20:]

        self._chat_running = True
        self.btn_mango_send.setEnabled(False)
        self.btn_mango_clear.setEnabled(False)
        self._chat_seq += 1
        current_seq = self._chat_seq

        future = self._chat_executor.submit(
            self.mango_assistant_service.chat,
            text,
            list(self._chat_history),
        )
        future.add_done_callback(lambda fut, seq=current_seq: self._on_chat_future_done(seq, fut))

    # 接收聊天任务完成回调并转发到主线程。
    def _on_chat_future_done(self, seq: int, future: Future[str]) -> None:
        try:
            payload: object = future.result()
        except Exception as exc:  # pragma: no cover
            payload = exc
        self._chat_signal_bus.chatReady.emit(seq, payload)

    # 在主线程处理聊天结果并更新界面。
    def _on_chat_ready(self, seq: int, payload: object) -> None:
        if seq != self._chat_seq:
            return

        self._chat_running = False
        self.btn_mango_send.setEnabled(True)
        self.btn_mango_clear.setEnabled(True)

        if isinstance(payload, Exception):
            reply = self.mango_assistant_service.build_local_reply(
                "这个季节推荐吃什么水果",
                reason=str(payload),
            )
        else:
            reply = str(payload).strip() or "我暂时没想好，换个问法再试试。"

        self._append_mango_line("芒小果", reply)
        self._chat_history.append({"role": "assistant", "content": reply})
        self._chat_history = self._chat_history[-20:]

    # 清空聊天记录和上下文状态。
    def on_mango_clear_clicked(self) -> None:
        if self._chat_running:
            QMessageBox.information(self.window, "请稍候", "芒小果正在回复，稍后再清空。")
            return
        self._init_mango_assistant()

    # 向运行日志区域追加文本。
    def _append_log(self, text: str) -> None:
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.run_log.appendPlainText(f"[{stamp}] {text}")
        LOGGER.info(text)

    # 读取并注入密钥后生成地图 HTML 内容。
    def _build_map_html_content(self, map_html_path: Path | None = None) -> str:
        actual_path = map_html_path or self.map_html_path
        if not actual_path.exists():
            raise FileNotFoundError(f"地图模板不存在: {actual_path}")

        html = actual_path.read_text(encoding="utf-8")
        js_key = self.app_config.amap.js_key.strip()
        security_js_code = self.app_config.amap.security_js_code.strip()

        html = html.replace("__AMAP_KEY__", js_key)
        html = html.replace("__AMAP_SECURITY_JS_CODE__", security_js_code)
        return html

    # 将地图 HTML 内容加载到主地图视图。
    def _load_map_html(self) -> None:
        html = self._build_map_html_content(self.map_html_path)
        self._set_map_html_to_view(self.map_view, html, self.map_html_path)

        js_key = self.app_config.amap.js_key.strip()
        security_js_code = self.app_config.amap.security_js_code.strip()

        if not js_key or not security_js_code:
            self._append_log("未配置高德 JS Key / securityJsCode，底图可能无法加载。")

    # 把 HTML 字符串写入指定地图视图。
    def _set_map_html_to_view(
        self,
        map_view: QWebEngineView,
        html: str,
        html_path: Path | None = None,
    ) -> None:
        # 指定 baseUrl，确保 map.html 内相对资源可加载。
        actual_path = html_path or self.map_html_path
        base_url = QUrl.fromLocalFile(str(actual_path.parent.resolve()) + "/")
        if map_view is self.map_view:
            self.map_ready = False
        map_view.setHtml(html, base_url)

    # 处理地图页面加载完成事件。
    def _on_map_load_finished(self, ok: bool) -> None:
        if ok:
            self._append_log("地图页面加载完成，等待 WebChannel 就绪。")
        else:
            self._append_log("地图页面加载失败，请检查 map.html。")
            self.window.statusBar().showMessage("地图页面加载失败")

    # 处理前端地图主动上报的就绪事件。
    def _on_map_ready(self) -> None:
        self.map_ready = True
        self._append_log("地图桥接完成，可接收路径。")
        self.window.statusBar().showMessage("地图已就绪")

        if self.pending_payload is not None:
            self.map_bridge.send_payload(self.pending_payload)
            self._append_log("已发送缓存路径到地图。")
            self.pending_payload = None

    # 记录前端地图回传的日志消息。
    def _on_js_log(self, message: str) -> None:
        self._append_log(f"地图消息: {message}")

    # 估算当前策略的计算时长用于假进度条展示。
    def _estimate_compute_seconds(self, strategy_source: str, strategy_name: str) -> float:
        """按当前策略与设置估算一个展示用时（仅用于 UI 展示，不参与算法）。"""
        source = strategy_source.strip()
        algo_name = strategy_name.strip()

        if source == self._STRATEGY_SOURCE_CUSTOM:
            estimate_s = 2.1
            estimate_s += max(0, self.route_service.custom_candidate_max_paths_per_strategy - 1) * 0.45
            if self.route_service.custom_candidate_use_tmcs:
                estimate_s += 0.25
            if self.route_service.custom_candidate_enable_divergence:
                estimate_s += 0.75

            densify = max(0.0, float(self.route_service.custom_candidate_densify_max_segment_m))
            if densify > 0:
                estimate_s += min(0.8, max(0.1, (80.0 / max(20.0, densify)) * 0.2))

            if "土豪" in algo_name:
                estimate_s += 0.2
        else:
            estimate_s = 1.4
            if "综合推荐" in algo_name or "多路径" in algo_name:
                estimate_s += 0.4
            if "躲避拥堵" in algo_name:
                estimate_s += 0.2

        return max(0.8, min(8.0, round(estimate_s, 1)))

    # 启动模拟计算进度条动画。
    def _start_fake_progress(self, expected_s: float) -> None:
        """启动假的进度条动画。"""
        self._progress_expected_s = max(0.8, float(expected_s))
        self._progress_started_at = time.perf_counter()
        self._progress_last_value = 0
        self.progress_bar_compute.setValue(0)
        self.label_estimated_compute_value.setText(f"约 {self._progress_expected_s:.1f} 秒")
        self.progress_timer.start()

    # 推进模拟计算进度条的当前状态。
    def _tick_fake_progress(self) -> None:
        """根据已过时间推进到 90% 左右，等待真实结果后再补到 100%。"""
        if not self._route_running:
            return

        elapsed_s = max(0.0, time.perf_counter() - self._progress_started_at)
        ratio = elapsed_s / max(0.8, self._progress_expected_s)
        target = int(min(90.0, ratio * 90.0))
        if ratio >= 1.0:
            target = max(target, min(97, self._progress_last_value + 1))

        if target > self._progress_last_value:
            self._progress_last_value = target
            self.progress_bar_compute.setValue(target)

    # 用真实耗时收尾模拟进度展示。
    def _finish_fake_progress(self, actual_ms: float) -> None:
        """规划结束后把进度补满，并显示预计/实际耗时。"""
        self.progress_timer.stop()
        self._progress_last_value = 100
        self.progress_bar_compute.setValue(100)
        actual_s = max(0.0, float(actual_ms) / 1000.0)
        self.label_estimated_compute_value.setText(
            f"预计 {self._progress_expected_s:.1f} 秒 / 实际 {actual_s:.2f} 秒"
        )

    # 将进度条与耗时展示重置为初始状态。
    def _reset_progress_display(self) -> None:
        """重置地图下方的计算进度展示。"""
        self.progress_timer.stop()
        self._progress_last_value = 0
        self.progress_bar_compute.setValue(0)
        self.label_estimated_compute_value.setText("待计算")

    # 接收路径规划任务完成回调并转发到主线程。
    def _on_route_future_done(self, seq: int, future: Future[RouteResult]) -> None:
        try:
            payload: object = future.result()
        except Exception as exc:  # pragma: no cover
            payload = exc
        self._route_signal_bus.routeReady.emit(seq, payload)

    # 在主线程处理路径规划结果并更新界面。
    def _on_route_ready(self, seq: int, payload: object) -> None:
        if seq != self._route_seq:
            return

        self._route_running = False
        self.btn_run.setEnabled(True)
        self.btn_reset.setEnabled(True)

        if isinstance(payload, RouteResult):
            result = payload
        else:
            elapsed_ms = max(0.0, (time.perf_counter() - self._progress_started_at) * 1000.0)
            message = f"路径规划失败: {payload}"
            result = RouteResult(
                path_points_wgs84=[],
                path_points_gcj02=[],
                total_distance_km=0.0,
                total_time_h=0.0,
                compute_ms=elapsed_ms,
                node_count=0,
                edge_count=0,
                status="error",
                message=message,
            )

        self._finish_fake_progress(result.compute_ms)
        self._update_outputs(result)

        request = self._active_request
        if request is not None:
            payload_to_map = MapPayload.from_route_result(
                result=result,
                start_text=request.start_text,
                end_text=request.end_text,
                algorithm=request.algorithm,
                fruit_type=request.fruit_type,
            )
            self._send_to_map(payload_to_map)

        if result.status == "error":
            QMessageBox.critical(self.window, "规划失败", result.message)
            self.window.statusBar().showMessage("规划失败")
        else:
            self.window.statusBar().showMessage("规划完成")

        if request is not None:
            self._record_result_history(request, result, self._active_strategy_source)

        self._append_log(result.message)
        self._active_request = None
        self._active_strategy_source = self._STRATEGY_SOURCE_AMAP

    # 记录一次路径规划结果到历史缓存。
    def _record_result_history(
        self,
        request: RouteRequest,
        result: RouteResult,
        strategy_source: str,
    ) -> None:
        entry: dict[str, object] = {
            "timestamp": QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss"),
            "start_text": request.start_text,
            "end_text": request.end_text,
            "strategy_source": strategy_source,
            "algorithm": request.algorithm,
            "fruit_type": request.fruit_type,
            "transport_mode": request.transport_mode,
            "status": result.status,
            "compute_ms": float(result.compute_ms),
            "total_distance_km": float(result.total_distance_km),
            "total_time_h": float(result.total_time_h),
            "node_count": int(result.node_count),
            "edge_count": int(result.edge_count),
            "message": result.message,
            "freshness_at_arrival": result.freshness_at_arrival,
            "freshness_delta_to_100": result.freshness_delta_to_100,
        }
        if isinstance(result.debug_payload, dict):
            entry["debug_payload_path"] = self._persist_result_detail_payload(entry, result.debug_payload)
        self._result_history.append(entry)
        self._save_result_history()

    # 清空右侧结果输出区域。
    def _clear_outputs(self) -> None:
        self.edit_eta.clear()
        self.edit_compute.clear()
        self.edit_distance.clear()
        self.edit_cost.clear()
        self.edit_nodes.clear()
        self.edit_edges.clear()
        self.edit_freshness.clear()
        self.edit_freshness_delta.clear()
        self.edit_status.setText("等待计算")

    # 校验用户输入是否完整且有效。
    def _validate_inputs(self) -> bool:
        if not self.line_edit_start.text().strip():
            QMessageBox.warning(self.window, "输入缺失", "请填写起点")
            return False
        if not self.line_edit_end.text().strip():
            QMessageBox.warning(self.window, "输入缺失", "请填写终点")
            return False
        return True

    # 用路径规划结果刷新界面输出字段。
    def _update_outputs(self, result: RouteResult) -> None:
        self.edit_eta.setText(f"{result.total_time_h:.2f} 小时")
        self.edit_compute.setText(f"{result.compute_ms:.2f} ms")
        self.edit_distance.setText(f"{result.total_distance_km:.2f} km")

        # 第一版成本为简化估算，后续可按车型/冷链参数替换。
        estimated_cost = result.total_distance_km * (2.1 + self.spin_load.value() * 0.05)
        self.edit_cost.setText(f"¥ {estimated_cost:.2f}")

        self.edit_nodes.setText(str(result.node_count))
        self.edit_edges.setText(str(result.edge_count))
        freshness_text = "-" if result.freshness_at_arrival is None else f"{result.freshness_at_arrival:.2f}"
        delta_text = "-" if result.freshness_delta_to_100 is None else f"{result.freshness_delta_to_100:.2f}"
        self.edit_freshness.setText(freshness_text)
        self.edit_freshness_delta.setText(delta_text)
        self.edit_status.setText(result.status)

    # 将地图展示载荷发送到前端地图页面。
    def _send_to_map(self, payload: MapPayload) -> None:
        if len(payload.points) < 2:
            self._append_log("路径点不足，跳过地图渲染。")
            return

        if self.map_ready:
            self.map_bridge.send_payload(payload)
            self._append_log("路径已发送到地图。")
        else:
            self.pending_payload = payload
            self._append_log("地图未就绪，已缓存路径，稍后自动渲染。")

    # 响应开始计算按钮并发起路径规划任务。
    def on_run_clicked(self) -> None:
        if self._route_running:
            QMessageBox.information(self.window, "正在计算", "当前已有计算任务，请稍候。")
            return

        if not self._validate_inputs():
            return

        normalized_start = self._normalize_place_text(self.line_edit_start.text())
        normalized_end = self._normalize_place_text(self.line_edit_end.text())
        self.line_edit_start.setText(normalized_start)
        self.line_edit_end.setText(normalized_end)

        self._remember_location(normalized_start)
        self._remember_location(normalized_end)

        request = RouteRequest(
            start_text=normalized_start,
            end_text=normalized_end,
            algorithm=self.combo_algorithm.currentText().strip(),
            fruit_type=self.combo_fruit.currentText().strip(),
            transport_mode=self.combo_transport.currentText().strip(),
            depart_at=self.datetime_depart.dateTime().toPython(),
            load_ton=float(self.spin_load.value()),
        )

        self._append_log(
            f"开始规划: {request.start_text} -> {request.end_text}, 来源={self.combo_strategy_source.currentText().strip()}, "
            f"策略={request.algorithm}, 水果={request.fruit_type}, 运输={request.transport_mode}, 载重={request.load_ton:.1f} 吨"
        )
        strategy_source = self.combo_strategy_source.currentText().strip() or self._STRATEGY_SOURCE_AMAP
        estimate_s = self._estimate_compute_seconds(strategy_source, request.algorithm)
        self._start_fake_progress(estimate_s)

        self._route_running = True
        self._active_request = request
        self._active_strategy_source = strategy_source
        self._route_seq += 1
        current_seq = self._route_seq

        self.btn_run.setEnabled(False)
        self.btn_reset.setEnabled(False)
        self.edit_status.setText("计算中")
        self.window.statusBar().showMessage("规划计算中...")

        future = self._route_executor.submit(self.route_service.plan_route, request)
        future.add_done_callback(lambda fut, seq=current_seq: self._on_route_future_done(seq, fut))

    # 重置输入项、输出项和地图展示状态。
    def on_reset_clicked(self) -> None:
        if self._route_running:
            QMessageBox.information(self.window, "正在计算", "当前任务尚未结束，暂不支持重置。")
            return

        self.line_edit_start.clear()
        self.line_edit_end.clear()
        self._init_strategy_options()
        self.combo_fruit.setCurrentIndex(0)
        self.combo_transport.setCurrentIndex(0)
        self.datetime_depart.setDateTime(QDateTime.currentDateTime())
        self.spin_load.setValue(5.0)

        self.pending_payload = None
        self.map_view.page().runJavaScript("window.clearRoute && window.clearRoute();")
        self._set_suggestion_list("start", [], show_popup=False)
        self._set_suggestion_list("end", [], show_popup=False)

        self._clear_outputs()
        self._reset_progress_display()
        self.run_log.clear()
        self._append_log("参数已重置。")
        self.window.statusBar().showMessage("参数已重置")

    # 关闭后台执行器并清理控制器资源。
    def shutdown(self) -> None:
        """应用退出前释放后台线程资源。"""
        self.progress_timer.stop()
        self._route_executor.shutdown(wait=False, cancel_futures=True)
        self._suggest_executor.shutdown(wait=False, cancel_futures=True)
        self._chat_executor.shutdown(wait=False, cancel_futures=True)
