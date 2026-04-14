from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import time
from typing import Type, TypeVar

from PySide6.QtCore import QDateTime, QFile, QObject, QStringListModel, QTimer, Qt, QUrl, Signal
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
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
    QSplitter,
    QSpinBox,
    QWidget,
)

from src.app.config_loader import AppConfig
from src.app.map_bridge import MapBridge
from src.models.map_payload import MapPayload
from src.models.route_request import RouteRequest
from src.models.route_result import RouteResult
from src.services.place_suggestion_service import PlaceSuggestionService, SuggestionItem
from src.services.route_planning_service import RoutePlanningService


LOGGER = logging.getLogger(__name__)
TWidget = TypeVar("TWidget", bound=QWidget)


class _SuggestionSignalBus(QObject):
    suggestionsReady = Signal(str, int, object)


class _RouteSignalBus(QObject):
    routeReady = Signal(int, object)


@dataclass(frozen=True, slots=True)
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

    def __init__(
        self,
        project_root: Path,
        app_config: AppConfig,
        route_service: RoutePlanningService,
        place_suggestion_service: PlaceSuggestionService | None = None,
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

        self.ui_path = self.project_root / self.app_config.ui.file
        self.map_html_path = self.project_root / self.app_config.ui.map_html
        self.settings_ui_path = self.project_root / "ui" / "设置界面.ui"

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

    def _load_dialog_ui(self, ui_path: Path) -> QDialog:
        if not ui_path.exists():
            raise FileNotFoundError(f"设置界面文件不存在: {ui_path}")

        qfile = QFile(str(ui_path))
        if not qfile.open(QFile.ReadOnly):
            raise RuntimeError(f"设置界面打开失败: {ui_path}")

        loader = QUiLoader()
        loaded = loader.load(qfile)
        qfile.close()

        if loaded is None or not isinstance(loaded, QDialog):
            raise RuntimeError("设置界面根节点必须是 QDialog。")
        return loaded

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

    def _require_widget(self, name: str, widget_type: Type[TWidget]) -> TWidget:
        widget = self.window.findChild(widget_type, name)
        if widget is None:
            raise RuntimeError(f"缺少必要控件: {name}")
        return widget

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

    def _on_location_text_edited(self, field: str, text: str) -> None:
        keyword = text.strip()
        self._pending_keyword[field] = keyword
        if not keyword:
            self._set_suggestion_list(field, [], show_popup=False)
            return

        timer = self.start_suggest_timer if field == "start" else self.end_suggest_timer
        timer.start()

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

    def _on_completion_selected(self, field: str, display_text: str) -> None:
        mapping = self._display_to_value.get(field, {})
        value = mapping.get(display_text, display_text)
        line_edit = self.line_edit_start if field == "start" else self.line_edit_end
        line_edit.setText(value)
        line_edit.setCursorPosition(len(value))

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

    def _suggest_from_recent(self, keyword: str, limit: int) -> list[SuggestionItem]:
        kw = keyword.lower()
        prefix = [x for x in self._recent_locations if x.lower().startswith(kw)]
        contain = [x for x in self._recent_locations if kw in x.lower() and x not in prefix]
        merged = (prefix + contain)[:limit]
        return [
            SuggestionItem(text=item, source=PlaceSuggestionService.SOURCE_HISTORY)
            for item in merged
        ]

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

    def _remember_location(self, value: str) -> None:
        text = value.strip()
        if not text:
            return
        if text in self._recent_locations:
            self._recent_locations.remove(text)
        self._recent_locations.insert(0, text)
        if len(self._recent_locations) > 80:
            self._recent_locations = self._recent_locations[:80]

    def _normalize_place_text(self, value: str) -> str:
        """清理输入末尾的来源标签，例如“北京南站 [高德]”。"""
        text = value.strip()
        text = self._SOURCE_SUFFIX_PATTERN.sub("", text).strip()
        return text

    def _setup_map_bridge(self) -> None:
        # 允许本地 HTML（setHtml）加载远程 JS/CSS 资源，否则高德 loader.js 无法加载。
        settings = self.map_view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)

        self.map_bridge = MapBridge()
        self.map_channel = QWebChannel(self.map_view.page())
        self.map_channel.registerObject("pyBridge", self.map_bridge)
        self.map_view.page().setWebChannel(self.map_channel)

        self.map_view.loadFinished.connect(self._on_map_load_finished)
        self.map_bridge.mapReady.connect(self._on_map_ready)
        self.map_bridge.jsLog.connect(self._on_js_log)

    def _init_ui_state(self) -> None:
        self._init_strategy_options()
        self.datetime_depart.setDateTime(QDateTime.currentDateTime())
        self._clear_outputs()
        self._reset_progress_display()
        self._apply_main_layout_ratio()
        self._load_map_html()
        self._append_log("界面已启动，等待输入。")

    def _apply_main_layout_ratio(self) -> None:
        """统一三栏布局比例，保证地图区优先展示。"""
        self.splitter_main.setStretchFactor(0, 26)
        self.splitter_main.setStretchFactor(1, 48)
        self.splitter_main.setStretchFactor(2, 26)
        self.splitter_main.setSizes([360, 860, 400])

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
            fallback = "自研-Dijkstra" if source == self._STRATEGY_SOURCE_CUSTOM else "速度优先"
            self.combo_algorithm.addItem(fallback)

        target = self.combo_algorithm.findText(preferred.strip())
        if target >= 0:
            self.combo_algorithm.setCurrentIndex(target)
        else:
            self.combo_algorithm.setCurrentIndex(0)

    def _on_strategy_source_changed(self, source_text: str) -> None:
        """策略来源切换后联动刷新具体策略。"""
        self._reload_strategy_options()
        show_text = source_text.strip() or self._STRATEGY_SOURCE_AMAP
        self._append_log(f"已切换策略来源: {show_text}")

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

        self.window.statusBar().showMessage("设置已应用")
        self._append_log(
            f"设置已应用({source_tag}): 每策略候选={self.route_service.custom_candidate_max_paths_per_strategy}, "
            f"TMCS={'开' if self.route_service.custom_candidate_use_tmcs else '关'}, "
            f"加密阈值={self.route_service.custom_candidate_densify_max_segment_m:.1f}m, "
            f"候选发散={'开' if self.route_service.custom_candidate_enable_divergence else '关'}, "
            f"锚点={self.route_service.custom_candidate_divergence_anchor_ratios}, "
            f"偏移={self.route_service.custom_candidate_divergence_offsets_m}"
        )

    def on_settings_clicked(self) -> None:
        dialog = self._load_dialog_ui(self.settings_ui_path)
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

    def _connect_signals(self) -> None:
        self.combo_strategy_source.currentTextChanged.connect(self._on_strategy_source_changed)
        self.btn_settings.clicked.connect(self.on_settings_clicked)
        self.btn_run.clicked.connect(self.on_run_clicked)
        self.btn_reset.clicked.connect(self.on_reset_clicked)

    def _append_log(self, text: str) -> None:
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.run_log.appendPlainText(f"[{stamp}] {text}")
        LOGGER.info(text)

    def _load_map_html(self) -> None:
        if not self.map_html_path.exists():
            raise FileNotFoundError(f"地图模板不存在: {self.map_html_path}")

        html = self.map_html_path.read_text(encoding="utf-8")
        js_key = self.app_config.amap.js_key.strip()
        security_js_code = self.app_config.amap.security_js_code.strip()

        html = html.replace("__AMAP_KEY__", js_key)
        html = html.replace("__AMAP_SECURITY_JS_CODE__", security_js_code)

        # 指定 baseUrl，确保 map.html 内相对资源可加载。
        base_url = QUrl.fromLocalFile(str(self.map_html_path.parent.resolve()) + "/")
        self.map_ready = False
        self.map_view.setHtml(html, base_url)

        if not js_key or not security_js_code:
            self._append_log("未配置高德 JS Key / securityJsCode，底图可能无法加载。")

    def _on_map_load_finished(self, ok: bool) -> None:
        if ok:
            self._append_log("地图页面加载完成，等待 WebChannel 就绪。")
        else:
            self._append_log("地图页面加载失败，请检查 map.html。")
            self.window.statusBar().showMessage("地图页面加载失败")

    def _on_map_ready(self) -> None:
        self.map_ready = True
        self._append_log("地图桥接完成，可接收路径。")
        self.window.statusBar().showMessage("地图已就绪")

        if self.pending_payload is not None:
            self.map_bridge.send_payload(self.pending_payload)
            self._append_log("已发送缓存路径到地图。")
            self.pending_payload = None

    def _on_js_log(self, message: str) -> None:
        self._append_log(f"地图消息: {message}")

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

    def _start_fake_progress(self, expected_s: float) -> None:
        """启动假的进度条动画。"""
        self._progress_expected_s = max(0.8, float(expected_s))
        self._progress_started_at = time.perf_counter()
        self._progress_last_value = 0
        self.progress_bar_compute.setValue(0)
        self.label_estimated_compute_value.setText(f"约 {self._progress_expected_s:.1f} 秒")
        self.progress_timer.start()

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

    def _finish_fake_progress(self, actual_ms: float) -> None:
        """规划结束后把进度补满，并显示预计/实际耗时。"""
        self.progress_timer.stop()
        self._progress_last_value = 100
        self.progress_bar_compute.setValue(100)
        actual_s = max(0.0, float(actual_ms) / 1000.0)
        self.label_estimated_compute_value.setText(
            f"预计 {self._progress_expected_s:.1f} 秒 / 实际 {actual_s:.2f} 秒"
        )

    def _reset_progress_display(self) -> None:
        """重置地图下方的计算进度展示。"""
        self.progress_timer.stop()
        self._progress_last_value = 0
        self.progress_bar_compute.setValue(0)
        self.label_estimated_compute_value.setText("待计算")

    def _on_route_future_done(self, seq: int, future: Future[RouteResult]) -> None:
        try:
            payload: object = future.result()
        except Exception as exc:  # pragma: no cover
            payload = exc
        self._route_signal_bus.routeReady.emit(seq, payload)

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

        self._append_log(result.message)
        self._active_request = None
        self._active_strategy_source = self._STRATEGY_SOURCE_AMAP

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

    def _validate_inputs(self) -> bool:
        if not self.line_edit_start.text().strip():
            QMessageBox.warning(self.window, "输入缺失", "请填写起点")
            return False
        if not self.line_edit_end.text().strip():
            QMessageBox.warning(self.window, "输入缺失", "请填写终点")
            return False
        return True

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

    def shutdown(self) -> None:
        """应用退出前释放后台线程资源。"""
        self.progress_timer.stop()
        self._route_executor.shutdown(wait=False, cancel_futures=True)
        self._suggest_executor.shutdown(wait=False, cancel_futures=True)
