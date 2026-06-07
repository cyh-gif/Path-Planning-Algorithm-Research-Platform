"""API 请求与响应模型定义，负责 HTTP 数据和领域对象之间的转换。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.models.route_request import RouteRequest
from src.models.route_result import RouteResult
from src.services.place_suggestion_service import SuggestionItem


class RoutePlanRequest(BaseModel):
    model_config = ConfigDict(
        title="路径规划请求",
        json_schema_extra={
            "example": {
                "start_text": "北京市海淀区新发地农产品市场",
                "end_text": "上海市浦东新区江桥批发市场",
                "algorithm": "目标保鲜字典序A*算法(TF-LA*)",
                "fruit_type": "香蕉",
                "transport_mode": "公路冷链",
                "depart_at": "2026-05-24T08:30:00",
                "load_ton": 8.0,
            }
        },
    )

    start_text: str = Field(
        ...,
        min_length=1,
        title="起点",
        description="运输任务的起始位置，可填写城市、区县、市场或详细地址。",
        examples=["北京市海淀区新发地农产品市场"],
    )
    end_text: str = Field(
        ...,
        min_length=1,
        title="终点",
        description="运输任务的目的地位置，可填写城市、区县、市场或详细地址。",
        examples=["上海市浦东新区江桥批发市场"],
    )
    algorithm: str = Field(
        ...,
        min_length=1,
        title="算法策略",
        description="路径规划所使用的算法名称，建议先通过 /api/meta/options 获取可用选项。",
        examples=["目标保鲜字典序A*算法(TF-LA*)"],
    )
    fruit_type: str = Field(
        ...,
        min_length=1,
        title="水果类型",
        description="参与保鲜衰减与运输时效计算的水果类别。",
        examples=["香蕉"],
    )
    transport_mode: str = Field(
        ...,
        min_length=1,
        title="运输方式",
        description="运输组织方式，例如公路冷链、公路常温或多式联运。",
        examples=["公路冷链"],
    )
    depart_at: datetime = Field(
        ...,
        title="出发时间",
        description="计划出发的日期时间，使用 ISO 8601 格式。",
        examples=["2026-05-24T08:30:00"],
    )
    load_ton: float = Field(
        ...,
        ge=0.0,
        title="载重吨数",
        description="本次运输装载重量，单位为吨。",
        examples=[8.0],
    )

    def to_domain(self) -> RouteRequest:
        # 将 API 请求模型转换为业务层使用的路径规划请求对象。
        return RouteRequest(
            start_text=self.start_text.strip(),
            end_text=self.end_text.strip(),
            algorithm=self.algorithm.strip(),
            fruit_type=self.fruit_type.strip(),
            transport_mode=self.transport_mode.strip(),
            depart_at=self.depart_at,
            load_ton=float(self.load_ton),
        )


class RoutePlanResponse(BaseModel):
    model_config = ConfigDict(
        title="路径规划结果",
        json_schema_extra={
            "example": {
                "path_points_wgs84": [[116.397428, 39.90923], [121.473701, 31.230416]],
                "path_points_gcj02": [[116.403963, 39.915119], [121.480237, 31.236305]],
                "total_distance_km": 1218.6,
                "total_time_h": 14.2,
                "compute_ms": 18.4,
                "node_count": 186,
                "edge_count": 412,
                "status": "ok",
                "message": "路径规划成功",
                "freshness_at_arrival": 96.8,
                "freshness_delta_to_100": 3.2,
            }
        },
    )

    path_points_wgs84: list[list[float]] = Field(
        title="WGS84轨迹点",
        description="用于通用地图展示的经纬度轨迹点序列，格式为 [longitude, latitude]。",
    )
    path_points_gcj02: list[list[float]] = Field(
        title="GCJ02轨迹点",
        description="适配国内地图服务展示的经纬度轨迹点序列，格式为 [longitude, latitude]。",
    )
    total_distance_km: float = Field(title="总里程(公里)", description="规划路径总距离，单位为公里。")
    total_time_h: float = Field(title="总耗时(小时)", description="规划路径总运输时间，单位为小时。")
    compute_ms: float = Field(title="计算耗时(毫秒)", description="算法本次求解消耗的计算时间。")
    node_count: int = Field(title="节点数", description="参与本次求解的图节点数量。")
    edge_count: int = Field(title="边数", description="参与本次求解的图边数量。")
    status: str = Field(title="状态", description="接口处理结果状态，例如 ok 或 error。")
    message: str = Field(title="说明", description="对本次规划结果的补充说明。")
    freshness_at_arrival: float | None = Field(
        default=None,
        title="到达保鲜度",
        description="到达终点时的预计保鲜度，百分制。",
    )
    freshness_delta_to_100: float | None = Field(
        default=None,
        title="距离满分损失",
        description="相较于 100 分保鲜度的损失值，数值越小越好。",
    )

    @classmethod
    def from_domain(cls, result: RouteResult) -> "RoutePlanResponse":
        # 将业务层的路径规划结果转换为标准 API 响应模型。
        return cls(**asdict(result))


class SuggestRequest(BaseModel):
    keyword: str = Field(..., min_length=1, title="关键词", description="待联想的地点关键词。")
    limit: int = Field(12, ge=1, le=30, title="返回数量", description="建议返回的候选条数。")


class SuggestItemResponse(BaseModel):
    text: str = Field(title="候选文本", description="地点联想候选项文本。")
    source: str = Field(title="来源", description="该候选项的来源服务。")

    @classmethod
    def from_domain(cls, item: SuggestionItem) -> "SuggestItemResponse":
        # 将地点联想领域对象转换为接口响应项。
        return cls(text=item.text, source=item.source)


class ChatRequest(BaseModel):
    user_text: str = Field(..., min_length=1, title="用户输入", description="当前用户消息内容。")
    history: list[dict[str, str]] = Field(
        default_factory=list,
        title="对话历史",
        description="上下文历史消息列表，每项通常包含 role 与 content 字段。",
    )


class ChatResponse(BaseModel):
    reply: str = Field(title="回复内容", description="智能助手返回的文本回复。")


class HealthResponse(BaseModel):
    status: str = Field(title="服务状态", description="服务运行状态。")
    app_name: str = Field(title="应用名称", description="当前应用或助手名称。")


class ApiOptionsResponse(BaseModel):
    default_strategy: str = Field(title="默认策略", description="系统当前默认采用的路径规划策略。")
    amap_strategies: dict[str, int] = Field(
        title="高德策略映射",
        description="高德官方路径策略名称与策略编码的映射关系。",
    )
    custom_algorithms: dict[str, str] = Field(
        title="自研算法映射",
        description="自研算法展示名称与内部标识的映射关系。",
    )
    fruit_types: list[str] = Field(title="水果类型列表", description="当前配置支持的水果类别。")
    transport_modes: list[str] = Field(title="运输方式列表", description="当前配置支持的运输方式。")
    candidate_options: dict[str, Any] = Field(
        title="候选项配置",
        description="候选路径筛选与搜索的相关配置项。",
    )
