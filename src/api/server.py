"""HTTP API 主入口，负责暴露路径规划、地点联想和助手对话接口。
http://101.37.116.44/planner
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, Query
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from src.api.schemas import (
    ApiOptionsResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    RoutePlanRequest,
    RoutePlanResponse,
    SuggestItemResponse,
)
from src.app.bootstrap import AppServices, build_services

_DOCS_JS_I18N = """
<script>
const zhMap = new Map([
  ["Schemas", "数据模型"],
  ["Expand all", "全部展开"],
  ["Collapse all", "全部收起"],
  ["Try it out", "在线调试"],
  ["Execute", "发送请求"],
  ["Cancel", "取消"],
  ["Clear", "清空"],
  ["Responses", "响应结果"],
  ["Parameters", "请求参数"],
  ["Request body", "请求体"],
  ["Response body", "响应体"],
  ["Response headers", "响应头"],
  ["Server response", "服务端响应"],
  ["Example Value", "示例值"],
  ["Model", "模型"],
  ["No parameters", "无请求参数"],
  ["Available authorizations", "可用授权"],
  ["Authorize", "授权"],
  ["Download", "下载"],
  ["Description", "说明"]
]);

function translateNode(root) {
  const walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) {
    textNodes.push(walker.currentNode);
  }
  for (const node of textNodes) {
    const raw = node.nodeValue;
    if (!raw) continue;
    const trimmed = raw.trim();
    if (!trimmed || !zhMap.has(trimmed)) continue;
    node.nodeValue = raw.replace(trimmed, zhMap.get(trimmed));
  }

  document.querySelectorAll('input[placeholder="Search"]').forEach((el) => {
    el.placeholder = "搜索接口";
  });
}

window.addEventListener("load", () => {
  translateNode(document.body);
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
          translateNode(node);
        }
      });
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
});
</script>
"""


@lru_cache(maxsize=1)
def get_services() -> AppServices:
    # 延迟构建并缓存服务对象，供所有接口复用同一套业务能力。
    return build_services()


def render_web_template(template_path: Path) -> HTMLResponse:
    # 读取网页模板并注入高德前端密钥后返回浏览器可直接访问的页面。
    services = get_services()
    html = template_path.read_text(encoding="utf-8")
    html = html.replace("__AMAP_KEY__", services.app_config.amap.js_key.strip())
    html = html.replace(
        "__AMAP_SECURITY_JS_CODE__",
        services.app_config.amap.security_js_code.strip(),
    )
    return HTMLResponse(content=html)


app = FastAPI(
    title="芒小果果运路径规划 API",
    version="1.0.0",
    description=(
        "面向水果运输场景的路径规划与智能助手接口服务。"
        "支持路径求解、地点联想、参数选项查询与对话辅助。"
    ),
    summary="水果冷链运输路径规划接口",
    docs_url=None,
    openapi_tags=[
        {"name": "系统状态", "description": "服务健康检查与运行状态接口。"},
        {"name": "基础数据", "description": "获取算法、运输方式、水果类型等基础选项。"},
        {"name": "路径规划", "description": "执行核心路径规划计算并返回运输结果。"},
        {"name": "地点联想", "description": "根据关键词返回地点联想候选项。"},
        {"name": "智能助手", "description": "与芒小果助手进行对话交互。"},
    ],
)


@app.get(
    "/",
    include_in_schema=False,
)
@app.get(
    "/route-planner",
    include_in_schema=False,
)
@app.get(
    "/planner",
    include_in_schema=False,
)
def route_planner_page() -> HTMLResponse:
    # 返回内置的简易网页规划页面，供浏览器直接调用 API 与地图展示。
    template_path = get_services().project_root / "web" / "route_planner.html"
    return render_web_template(template_path)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["系统状态"],
    summary="健康检查",
    description="用于确认 API 服务是否正常运行，并返回当前应用名称。",
)
def health() -> HealthResponse:
    # 返回服务健康状态与当前应用名称，供探活或部署检查使用。
    services = get_services()
    return HealthResponse(status="ok", app_name=services.app_config.assistant.name)


@app.get(
    "/api/meta/options",
    response_model=ApiOptionsResponse,
    tags=["基础数据"],
    summary="获取基础选项",
    description="返回算法策略、水果类型、运输方式以及候选路径配置等基础数据。",
)
def api_options() -> ApiOptionsResponse:
    # 汇总前端或调用方初始化所需的算法、运输方式和候选配置选项。
    services = get_services()
    route_service = services.route_service

    fruit_names: list[str] = []
    seen_fruits: set[str] = set()
    for profile in route_service.fruit_profiles.values():
        if profile.name not in seen_fruits:
            seen_fruits.add(profile.name)
            fruit_names.append(profile.name)

    transport_modes = list(route_service.transport_mode_multipliers.keys())
    if not transport_modes:
        transport_modes = ["公路冷链", "公路常温", "铁路联运", "多式联运"]

    return ApiOptionsResponse(
        default_strategy=route_service.default_strategy,
        amap_strategies=dict(route_service.amap_strategy_map),
        custom_algorithms=dict(route_service.custom_algorithm_map),
        fruit_types=fruit_names,
        transport_modes=transport_modes,
        candidate_options=route_service.get_custom_candidate_options(),
    )


@app.post(
    "/api/route/plan",
    response_model=RoutePlanResponse,
    tags=["路径规划"],
    summary="执行路径规划",
    description=(
        "根据起点、终点、算法策略、水果类型、运输方式、出发时间与载重信息，"
        "计算运输路径、里程、耗时与到达保鲜度等结果。"
    ),
)
def plan_route(
    payload: Annotated[
        RoutePlanRequest,
        Body(
            description="路径规划请求体。建议先调用 /api/meta/options 获取可选算法与运输方式。",
        ),
    ],
) -> RoutePlanResponse:
    # 接收路径规划请求并转交业务层执行，再将领域结果转换为 API 响应。
    result = get_services().route_service.plan_route(payload.to_domain())
    return RoutePlanResponse.from_domain(result)


@app.get(
    "/api/place/suggest",
    response_model=list[SuggestItemResponse],
    tags=["地点联想"],
    summary="地点联想",
    description="根据输入关键词返回地点候选项，可用于前端输入框自动补全。",
)
def suggest_places(
    keyword: str = Query(..., min_length=1, description="待检索的地点关键词。", examples=["北京新发地"]),
    limit: int = Query(12, ge=1, le=30, description="返回候选项数量上限。", examples=[12]),
) -> list[SuggestItemResponse]:
    # 根据关键词返回地点联想候选项，供输入框自动补全使用。
    items = get_services().place_suggestion_service.suggest_with_source(keyword, limit)
    return [SuggestItemResponse.from_domain(item) for item in items]


@app.post(
    "/api/chat",
    response_model=ChatResponse,
    tags=["智能助手"],
    summary="助手对话",
    description="向芒小果助手发送消息，并获取基于上下文的文本回复。",
)
def chat(payload: ChatRequest) -> ChatResponse:
    # 调用芒小果助手完成一轮对话，并返回文本回复内容。
    reply = get_services().mango_assistant_service.chat(
        payload.user_text,
        payload.history,
    )
    return ChatResponse(reply=reply)


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui() -> HTMLResponse:
    # 生成自定义 Swagger 文档页，并注入界面中文化脚本。
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - 接口文档",
        swagger_ui_parameters={
            "docExpansion": "list",
            "defaultModelsExpandDepth": 1,
            "displayRequestDuration": True,
            "filter": True,
        },
    )
    html = response.body.decode("utf-8").replace("</body>", f"{_DOCS_JS_I18N}</body>")
    return HTMLResponse(content=html, status_code=response.status_code)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.server:app", host="127.0.0.1", port=8000, reload=True)
