"""应用装配模块。

本文件承担桌面端与 API 共享的 composition root 角色，
负责按统一顺序加载配置、初始化基础客户端，并组装路径规划、
地点联想和智能助手等核心服务对象。
"""


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.agent.mango_assistant_service import MangoAssistantService
from src.app.config_loader import AppConfig, load_app_config
from src.services.amap_web_service import AMapWebServiceClient
from src.services.place_suggestion_service import PlaceSuggestionService
from src.services.route_planning_service import RoutePlanningService
from src.utils.logger import setup_logging

#把分散的配置、客户端和服务对象集中初始化，形成一个可复用的运行环境。
@dataclass(slots=True)
# 汇总桌面端和 API 复用的服务对象，作为应用运行时上下文容器。
class AppServices:
    project_root: Path
    app_config: AppConfig
    amap_client: AMapWebServiceClient
    place_suggestion_service: PlaceSuggestionService
    route_service: RoutePlanningService
    mango_assistant_service: MangoAssistantService


# 解析仓库根目录，供配置、资源和日志路径定位使用。
def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# 按统一顺序装配配置、地图客户端、业务服务和助手服务。
def build_services(project_root: Path | None = None, setup_logs: bool = True) -> AppServices:
    project_root = project_root or resolve_project_root()
    app_config = load_app_config(project_root)

    if setup_logs:
        setup_logging(project_root, app_config.logging)

    amap_client = AMapWebServiceClient(
        web_service_key=app_config.amap.web_service_key,
        request_timeout_s=app_config.amap.timeout_s,
        retry=app_config.amap.retry,
        cache_ttl_s=app_config.amap.cache_ttl_s,
    )

    place_suggestion_service = PlaceSuggestionService(
        amap_key=app_config.amap.web_service_key or app_config.amap.js_key,
        request_timeout_s=app_config.amap.timeout_s,
        retry=app_config.amap.retry,
        cache_ttl_s=app_config.amap.cache_ttl_s,
    )

    route_service = RoutePlanningService(
        amap_client=amap_client,
        amap_strategy_map=app_config.routing.amap_strategy_map or {},
        custom_algorithm_map=app_config.routing.custom_algorithm_map or {},
        custom_candidate_strategy_codes=app_config.routing.custom_candidate_strategy_codes or [],
        custom_candidate_max_paths_per_strategy=app_config.routing.custom_candidate_max_paths_per_strategy,
        custom_candidate_use_tmcs=app_config.routing.custom_candidate_use_tmcs,
        custom_candidate_densify_max_segment_m=app_config.routing.custom_candidate_densify_max_segment_m,
        custom_candidate_enable_divergence=app_config.routing.custom_candidate_enable_divergence,
        custom_candidate_divergence_anchor_ratios=app_config.routing.custom_candidate_divergence_anchor_ratios,
        custom_candidate_divergence_offsets_m=app_config.routing.custom_candidate_divergence_offsets_m,
        default_strategy=app_config.routing.default_strategy,
        peak_hours=app_config.custom_time_dependent.peak_hours,
        peak_multiplier=app_config.custom_time_dependent.peak_multiplier,
        freshness_target=app_config.freshness.target,
        freshness_base_loss_per_hour=app_config.freshness.base_loss_per_hour,
        freshness_max_detour_ratio=app_config.freshness.max_detour_ratio,
        freshness_arbitration_scope=app_config.freshness.arbitration_scope,
        freshness_amap_compare_strategy_codes=app_config.freshness.amap_compare_strategy_codes or [],
        freshness_amap_compare_max_paths_per_strategy=(
            app_config.freshness.amap_compare_max_paths_per_strategy
        ),
        freshness_arbitration_time_budget_s=app_config.freshness.arbitration_time_budget_s,
        freshness_transport_mode_multipliers=app_config.freshness.transport_mode_multipliers or {},
        freshness_road_class_multipliers=app_config.freshness.road_class_multipliers or {},
        freshness_tmcs_status_multipliers=app_config.freshness.tmcs_status_multipliers or {},
        fruit_profile_json_path=project_root / app_config.freshness.fruit_profile_json,
    )

    mango_assistant_service = MangoAssistantService(
        agent_name=app_config.assistant.name,
        api_key=app_config.assistant.api_key,
        endpoint=app_config.assistant.endpoint,
        model=app_config.assistant.model,
        timeout_s=app_config.assistant.timeout_s,
        retry=app_config.assistant.retry,
    )

    return AppServices(
        project_root=project_root,
        app_config=app_config,
        amap_client=amap_client,
        place_suggestion_service=place_suggestion_service,
        route_service=route_service,
        mango_assistant_service=mango_assistant_service,
    )
