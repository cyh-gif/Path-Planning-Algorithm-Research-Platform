from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.app.config_loader import load_app_config
from src.app.controller import MainWindowController
from src.agent.mango_assistant_service import MangoAssistantService
from src.services.amap_web_service import AMapWebServiceClient
from src.services.route_planning_service import RoutePlanningService
from src.utils.logger import setup_logging


LOGGER = logging.getLogger(__name__)


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    app_config = load_app_config(project_root)

    setup_logging(project_root, app_config.logging)

    amap_client = AMapWebServiceClient(
        web_service_key=app_config.amap.web_service_key,
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

    app = QApplication(sys.argv)
    controller = MainWindowController(
        project_root=project_root,
        app_config=app_config,
        route_service=route_service,
        mango_assistant_service=mango_assistant_service,
    )
    app.aboutToQuit.connect(controller.shutdown)
    controller.window.show()

    LOGGER.info("应用启动完成")
    return app.exec()
