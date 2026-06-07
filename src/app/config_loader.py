"""应用配置加载模块。

本文件负责读取项目配置文件、环境变量覆盖项以及敏感配置，
并将原始配置数据整理为具备明确字段结构的 dataclass 对象，
供桌面端和 API 在启动时统一使用。
"""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os


@dataclass(slots=True)
# 描述主界面和地图页面资源路径配置。
class UiConfig:
    file: str = "ui/主界面.ui"
    map_html: str = "ui/map.html"


@dataclass(slots=True)
# 描述高德地图前后端访问所需的连接参数。
class AMapConfig:
    js_key: str = ""
    security_js_code: str = ""
    web_service_key: str = ""
    timeout_s: float = 1.2
    retry: int = 1
    cache_ttl_s: int = 45


@dataclass(slots=True)
# 描述路径规划策略和候选图构建相关配置。
class RoutingConfig:
    default_strategy: str = "速度优先"
    amap_strategy_map: dict[str, int] | None = None
    custom_algorithm_map: dict[str, str] | None = None
    custom_candidate_strategy_codes: list[int] | None = None
    custom_candidate_max_paths_per_strategy: int = 2
    custom_candidate_use_tmcs: bool = True
    custom_candidate_densify_max_segment_m: float = 80.0
    custom_candidate_enable_divergence: bool = False
    custom_candidate_divergence_anchor_ratios: list[float] | None = None
    custom_candidate_divergence_offsets_m: list[float] | None = None


@dataclass(slots=True)
# 描述高峰时段和时变路网参数配置。
class CustomTimeDependentConfig:
    peak_hours: list[int]
    peak_multiplier: float = 1.35


@dataclass(slots=True)
# 描述保鲜模型、仲裁范围与损耗参数配置。
class FreshnessConfig:
    target: float = 100.0
    base_loss_per_hour: float = 2.0
    max_detour_ratio: float = 1.35
    arbitration_scope: str = "amap_only"
    amap_compare_strategy_codes: list[int] | None = None
    amap_compare_max_paths_per_strategy: int = 3
    arbitration_time_budget_s: float = 9.0
    fruit_profile_json: str = "configs/fruit_profiles.json"
    transport_mode_multipliers: dict[str, float] | None = None
    road_class_multipliers: dict[str, float] | None = None
    tmcs_status_multipliers: dict[str, float] | None = None


@dataclass(slots=True)
# 描述日志级别与日志文件路径配置。
class LoggingConfig:
    level: str = "INFO"
    file: str = "results/logs/app.log"


@dataclass(slots=True)
# 描述智能助手模型访问与重试参数配置。
class AssistantConfig:
    name: str = "芒小果"
    provider: str = "tongyi"
    api_key: str = ""
    endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    model: str = "qwen-plus"
    timeout_s: float = 20.0
    retry: int = 1


@dataclass(slots=True)
# 汇总应用运行所需的全部子配置对象。
class AppConfig:
    ui: UiConfig
    amap: AMapConfig
    routing: RoutingConfig
    custom_time_dependent: CustomTimeDependentConfig
    freshness: FreshnessConfig
    assistant: AssistantConfig
    logging: LoggingConfig


# 将简单文本值解析为布尔、数字或普通字符串。
def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith(('"', "'")) and text.endswith(('"', "'")):
        return text[1:-1]

    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False

    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


# 读取并解析受限格式的 YAML 配置文件。
def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """轻量 YAML 解析器，仅支持 key/value 与缩进字典。"""
    if not path.exists():
        return {}

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, root)]

    for line in path.read_text(encoding="utf-8").splitlines():
        clean_line = line.lstrip("\ufeff")
        if not clean_line.strip() or clean_line.strip().startswith("#"):
            continue

        if ":" not in clean_line:
            continue

        indent = len(clean_line) - len(clean_line.lstrip(" "))
        key, raw_value = clean_line.strip().split(":", 1)

        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()

        current = stack[-1][1]
        value = raw_value.strip()

        if value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent + 2, child))
        else:
            current[key] = _parse_scalar(value)

    return root


# 递归合并基础配置与覆盖配置字典。
def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = _deep_merge_dict(base_value, override_value)
        else:
            merged[key] = override_value
    return merged


# 按键路径安全读取嵌套字典中的值。
def _deep_get(data: dict[str, Any], path: list[str], default: Any) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


# 从多个环境变量中选取第一个非空值作为覆盖配置。
def _pick_non_empty_env(env_names: list[str], fallback: str) -> str:
    for env_name in env_names:
        raw = os.getenv(env_name)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return fallback


# 返回内置的高德策略名称与策略编码映射。
def _default_amap_strategy_map() -> dict[str, int]:
    return {
        "速度优先": 0,
        "费用优先": 1,
        "常规最快": 2,
        "躲避拥堵": 12,
        "不走高速": 13,
        "少走高速": 6,
        "避免收费": 14,
        "综合推荐(多路径)": 10,
        "躲避拥堵且不走高速": 15,
        "避免收费且不走高速": 16,
        "躲避拥堵且避免收费": 17,
        "躲避拥堵且避免收费且不走高速": 18,
        "高速优先": 19,
        "高速优先且躲避拥堵": 20,
    }


# 返回界面展示名称与内部算法标识的默认映射。
def _default_custom_algorithm_map() -> dict[str, str]:
    return {
        "经典Dijkstra算法": "static_dijkstra",
        "时变Dijkstra": "time_dependent_dijkstra",
        "A*": "a_star",
        "贪心算法": "greedy_best_first",
        "土豪算法": "tycoon_longest_route",
        "保鲜优先算法": "freshness_first",
        "保鲜优先算法-迪杰斯特拉算法改进版": "freshness_dijkstra_improved",
        "目标保鲜偏差算法(ATD-LS)": "target_freshness_atd_ls",
        "目标保鲜K最短路算法(TF-KSP)": "target_freshness_tf_ksp",
        "目标保鲜字典序A*算法(TF-LA*)": "target_freshness_tf_la_star",
    }


# 将原始映射解析为字符串到整数的字典。
def _parse_int_map(raw_mapping: Any) -> dict[str, int]:
    if not isinstance(raw_mapping, dict):
        return {}
    parsed: dict[str, int] = {}
    for key, value in raw_mapping.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            parsed[name] = int(value)
        except (TypeError, ValueError):
            continue
    return parsed


# 将原始映射解析为字符串到字符串的字典。
def _parse_str_map(raw_mapping: Any) -> dict[str, str]:
    if not isinstance(raw_mapping, dict):
        return {}
    parsed: dict[str, str] = {}
    for key, value in raw_mapping.items():
        name = str(key).strip()
        val = str(value).strip()
        if not name or not val:
            continue
        parsed[name] = val
    return parsed


# 将原始映射解析为字符串到浮点数的字典。
def _parse_float_map(raw_mapping: Any) -> dict[str, float]:
    if not isinstance(raw_mapping, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, value in raw_mapping.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            parsed[name] = float(value)
        except (TypeError, ValueError):
            continue
    return parsed


# 将逗号分隔的高峰小时文本解析为整数列表。
def _parse_peak_hours(csv_text: str) -> list[int]:
    result: list[int] = []
    for token in csv_text.split(","):
        token = token.strip()
        if not token.isdigit():
            continue
        value = int(token)
        if 0 <= value <= 23:
            result.append(value)
    return result


# 将逗号分隔的策略编码文本解析为整数列表。
def _parse_strategy_codes(csv_text: str) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for token in csv_text.split(","):
        token = token.strip()
        if not token.isdigit():
            continue
        value = int(token)
        if value < 0 or value > 99:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


# 将比例文本解析为限定长度的浮点列表。
def _parse_ratio_list(csv_text: str, max_count: int = 2) -> list[float]:
    result: list[float] = []
    seen: set[float] = set()
    for token in csv_text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        value = round(min(max(value, 0.05), 0.95), 3)
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= max_count:
            break
    return sorted(result)


# 将偏移距离文本解析为限定长度的浮点列表。
def _parse_offset_list(csv_text: str, max_count: int = 2) -> list[float]:
    result: list[float] = []
    seen: set[float] = set()
    for token in csv_text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        value = round(min(max(value, 50.0), 3000.0), 1)
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= max_count:
            break
    return sorted(result)


# 将多种表现形式的值统一解析为布尔值。
def _parse_bool(raw_value: Any, default: bool) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        text = raw_value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off"}:
            return False
    return default


# 读取主配置与密钥配置，并组装为完整的应用配置对象。
def load_app_config(project_root: Path) -> AppConfig:
    app_data = _load_simple_yaml(project_root / "configs" / "app.yaml")
    secrets_data = _load_simple_yaml(project_root / "configs" / "secrets.yaml")
    data = _deep_merge_dict(app_data, secrets_data)

    ui = UiConfig(
        file=str(_deep_get(data, ["ui", "file"], "ui/主界面.ui")),
        map_html=str(_deep_get(data, ["ui", "map_html"], "ui/map.html")),
    )

    js_key = _pick_non_empty_env(
        ["AMAP_JS_KEY", "AMAP_KEY"],
        str(_deep_get(data, ["amap", "js_key"], "")),
    )
    security_js_code = _pick_non_empty_env(
        ["AMAP_SECURITY_JS_CODE"],
        str(_deep_get(data, ["amap", "security_js_code"], "")),
    )
    web_service_key = _pick_non_empty_env(
        ["AMAP_WEB_SERVICE_KEY", "AMAP_WS_KEY", "AMAP_KEY"],
        str(_deep_get(data, ["amap", "web_service_key"], "")),
    )

    amap_cfg = AMapConfig(
        js_key=js_key,
        security_js_code=security_js_code,
        web_service_key=web_service_key or js_key,
        timeout_s=float(_deep_get(data, ["amap", "timeout_s"], 1.2)),
        retry=int(_deep_get(data, ["amap", "retry"], 1)),
        cache_ttl_s=int(_deep_get(data, ["amap", "cache_ttl_s"], 45)),
    )

    legacy_amap_map = _deep_get(data, ["routing", "strategy_map"], {})
    amap_map = _parse_int_map(_deep_get(data, ["routing", "amap_strategy_map"], legacy_amap_map))
    if not amap_map:
        amap_map = _default_amap_strategy_map()

    custom_algo_map = _parse_str_map(_deep_get(data, ["routing", "custom_algorithm_map"], {}))
    if not custom_algo_map:
        custom_algo_map = _default_custom_algorithm_map()

    default_strategy = str(
        _deep_get(data, ["routing", "default_strategy"], "速度优先")
    ).strip() or "速度优先"
    if default_strategy not in amap_map and default_strategy not in custom_algo_map:
        default_strategy = next(iter(amap_map.keys()))

    routing_cfg = RoutingConfig(
        default_strategy=default_strategy,
        amap_strategy_map=amap_map,
        custom_algorithm_map=custom_algo_map,
        custom_candidate_strategy_codes=_parse_strategy_codes(
            str(
                _deep_get(
                    data,
                    ["routing", "custom_candidate_strategy_codes_csv"],
                    "0,12,13,14,19",
                )
            )
        ),
        custom_candidate_max_paths_per_strategy=int(
            _deep_get(data, ["routing", "custom_candidate_max_paths_per_strategy"], 2)
        ),
        custom_candidate_use_tmcs=_parse_bool(
            _deep_get(data, ["routing", "custom_candidate_use_tmcs"], True),
            True,
        ),
        custom_candidate_densify_max_segment_m=float(
            _deep_get(data, ["routing", "custom_candidate_densify_max_segment_m"], 80.0)
        ),
        custom_candidate_enable_divergence=_parse_bool(
            _deep_get(data, ["routing", "custom_candidate_enable_divergence"], False),
            False,
        ),
        custom_candidate_divergence_anchor_ratios=_parse_ratio_list(
            str(
                _deep_get(
                    data,
                    ["routing", "custom_candidate_divergence_anchor_ratios_csv"],
                    "0.35,0.65",
                )
            ),
            max_count=2,
        ),
        custom_candidate_divergence_offsets_m=_parse_offset_list(
            str(
                _deep_get(
                    data,
                    ["routing", "custom_candidate_divergence_offsets_m_csv"],
                    "300,600",
                )
            ),
            max_count=2,
        ),
    )

    peak_csv = str(
        _deep_get(data, ["custom_time_dependent", "peak_hours_csv"], "7,8,9,17,18,19")
    )
    peak_hours = _parse_peak_hours(peak_csv)
    if not peak_hours:
        peak_hours = [7, 8, 9, 17, 18, 19]

    custom_td_cfg = CustomTimeDependentConfig(
        peak_hours=peak_hours,
        peak_multiplier=float(_deep_get(data, ["custom_time_dependent", "peak_multiplier"], 1.35)),
    )

    freshness_cfg = FreshnessConfig(
        target=float(_deep_get(data, ["freshness", "target"], 100.0)),
        base_loss_per_hour=float(_deep_get(data, ["freshness", "base_loss_per_hour"], 2.0)),
        max_detour_ratio=float(_deep_get(data, ["freshness", "max_detour_ratio"], 1.35)),
        arbitration_scope=str(_deep_get(data, ["freshness", "arbitration_scope"], "amap_only")),
        amap_compare_strategy_codes=_parse_strategy_codes(
            str(_deep_get(data, ["freshness", "amap_compare_strategy_codes_csv"], ""))
        ),
        amap_compare_max_paths_per_strategy=int(
            _deep_get(data, ["freshness", "amap_compare_max_paths_per_strategy"], 3)
        ),
        arbitration_time_budget_s=float(
            _deep_get(data, ["freshness", "arbitration_time_budget_s"], 9.0)
        ),
        fruit_profile_json=str(
            _deep_get(data, ["freshness", "fruit_profile_json"], "configs/fruit_profiles.json")
        ),
        transport_mode_multipliers=_parse_float_map(
            _deep_get(data, ["freshness", "transport_mode_multipliers"], {})
        ),
        road_class_multipliers=_parse_float_map(
            _deep_get(data, ["freshness", "road_class_multipliers"], {})
        ),
        tmcs_status_multipliers=_parse_float_map(
            _deep_get(data, ["freshness", "tmcs_status_multipliers"], {})
        ),
    )

    logging_cfg = LoggingConfig(
        level=str(_deep_get(data, ["logging", "level"], "INFO")),
        file=str(_deep_get(data, ["logging", "file"], "results/logs/app.log")),
    )
    assistant_api_key = _pick_non_empty_env(
        ["TONGYI_API_KEY", "DASHSCOPE_API_KEY"],
        str(_deep_get(data, ["assistant", "api_key"], "")),
    )
    assistant_cfg = AssistantConfig(
        name=str(_deep_get(data, ["assistant", "name"], "芒小果")),
        provider=str(_deep_get(data, ["assistant", "provider"], "tongyi")),
        api_key=assistant_api_key,
        endpoint=str(
            _deep_get(
                data,
                ["assistant", "endpoint"],
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            )
        ),
        model=str(_deep_get(data, ["assistant", "model"], "qwen-plus")),
        timeout_s=float(_deep_get(data, ["assistant", "timeout_s"], 20.0)),
        retry=int(_deep_get(data, ["assistant", "retry"], 1)),
    )

    return AppConfig(
        ui=ui,
        amap=amap_cfg,
        routing=routing_cfg,
        custom_time_dependent=custom_td_cfg,
        freshness=freshness_cfg,
        assistant=assistant_cfg,
        logging=logging_cfg,
    )
