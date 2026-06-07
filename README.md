# Path-Planning-Algorithm-Research-Platform

基于高德地图 API、PySide6 桌面界面、FastAPI 接口和多种最短路径/保鲜优化算法构建的水果运输路径规划研究平台。

这个仓库既是一个可运行的应用项目，也是一个用于算法对比、论文展示和接口部署的实验平台。项目当前同时支持：

- 桌面端图形界面
- HTTP API 调用
- 简单网页路径规划页
- 多种自研路径规划算法对比
- 保鲜度相关的运输路径优化

---

## 1. 项目总体结构

项目采用“入口层 -> 配置/装配层 -> 业务服务层 -> 算法层 -> 表现层”的结构：

```text
main.py
src/
  app/         桌面应用入口、配置加载、控制器、地图桥接
  api/         FastAPI 接口层
  services/    业务服务层
  algorithms/  具体算法实现
  core/        图结构与算法公共结果模型
  utils/       通用工具
  agent/       智能助手服务

configs/       主配置、敏感配置模板、业务参数配置
ui/            Qt Designer 界面文件与嵌入地图页面
web/           浏览器端示例页面
data/          运行时缓存和本地状态
tests/         单元测试
docs/          论文配图、附录素材、报告资料
results/       日志、指标和运行产物
```

可以把它简单理解为：

- `src/app` 负责“桌面程序怎么启动、怎么交互”
- `src/api` 负责“外部程序怎么通过 HTTP 调用功能”
- `src/services` 负责“业务流程怎么组织”
- `src/algorithms` 负责“算法具体怎么求解”
- `src/core` 负责“算法运行所依赖的公共数据结构”

---

## 2. 运行模式

项目现在有三种主要使用方式。

### 2.1 桌面 GUI

从根目录启动：

```powershell
cd C:\Users\86182\Desktop\Algorithm
.\.venv\Scripts\python.exe .\main.py
```

作用：

- 打开 PySide6 桌面界面
- 输入起点、终点、算法、水果、运输方式等参数
- 在内嵌地图中显示路线
- 查看历史结果与详情

### 2.2 FastAPI 接口

从根目录启动：

```powershell
cd C:\Users\86182\Desktop\Algorithm
.\.venv\Scripts\python.exe -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload
```

常用地址：

- `/health`：健康检查
- `/docs`：Swagger 文档
- `/api/meta/options`：基础选项
- `/api/place/suggest`：地点联想
- `/api/route/plan`：路径规划
- `/api/chat`：智能助手

### 2.3 简单网页规划页

当 API 启动后，可直接通过浏览器访问：

- `/`
- `/planner`
- `/route-planner`

这些地址都映射到同一个网页规划页，用于直接在浏览器中调用 API 并显示地图结果。

---

## 3. 顶层目录与文件说明

### 3.1 根目录文件

- [main.py](C:\Users\86182\Desktop\Algorithm\main.py)
  作用：项目总入口，负责调用 `src.app.main.main()` 启动桌面应用。

- [README.md](C:\Users\86182\Desktop\Algorithm\README.md)
  作用：项目说明文档，介绍项目结构、运行方式和文件职责。

- [requirements.txt](C:\Users\86182\Desktop\Algorithm\requirements.txt)
  作用：Python 依赖清单，用于创建和安装运行环境。

- [LICENSE](C:\Users\86182\Desktop\Algorithm\LICENSE)
  作用：项目许可证文件。

- [项目文件作用说明.txt](C:\Users\86182\Desktop\Algorithm\项目文件作用说明.txt)
  作用：项目内各文件的中文用途说明，偏人工整理版。

- [uvicorn_api.out.log](C:\Users\86182\Desktop\Algorithm\uvicorn_api.out.log)
  作用：本地调试 API 时产生的输出日志。

- [uvicorn_api.err.log](C:\Users\86182\Desktop\Algorithm\uvicorn_api.err.log)
  作用：本地调试 API 时产生的错误日志。

- [uvicorn_planner_test.out.log](C:\Users\86182\Desktop\Algorithm\uvicorn_planner_test.out.log)
  作用：本地测试网页规划页时产生的输出日志。

- [uvicorn_planner_test.err.log](C:\Users\86182\Desktop\Algorithm\uvicorn_planner_test.err.log)
  作用：本地测试网页规划页时产生的错误日志。

### 3.2 非核心开发目录

- `.idea/`
  作用：PyCharm/IDEA 工程配置目录，属于本地开发环境文件。

- `.venv/`
  作用：本地 Python 虚拟环境，不属于业务代码本体。

---

## 4. 配置目录 `configs/`

### 文件说明

- [configs/app.yaml](C:\Users\86182\Desktop\Algorithm\configs\app.yaml)
  作用：主配置文件。
  内容包括：
  - UI 文件路径
  - 高德 API 参数的非敏感部分
  - 高德策略映射
  - 自研算法映射
  - 时变参数
  - 保鲜模型参数
  - 日志配置
  - 助手服务配置

- [configs/secrets.yaml](C:\Users\86182\Desktop\Algorithm\configs\secrets.yaml)
  作用：本地敏感配置文件。
  内容通常包括：
  - 高德 `js_key`
  - 高德 `security_js_code`
  - 高德 `web_service_key`
  - 大模型 `api_key`
  注意：这个文件应被 `.gitignore` 忽略，不应提交到仓库。

- [configs/secrets.example.yaml](C:\Users\86182\Desktop\Algorithm\configs\secrets.example.yaml)
  作用：敏感配置模板，用于告诉协作者该如何创建本地 `secrets.yaml`。

- [configs/fruit_profiles.json](C:\Users\86182\Desktop\Algorithm\configs\fruit_profiles.json)
  作用：水果保鲜模型参数数据文件。
  用于为不同水果提供保鲜衰减、运输场景相关参数，是保鲜优化算法的重要输入。

---

## 5. 数据目录 `data/`

### 5.1 `data/cache/`

这个目录主要保存运行时状态，而不是正式业务配置。

- [data/cache/ui_settings.json](C:\Users\86182\Desktop\Algorithm\data\cache\ui_settings.json)
  作用：保存上次 GUI 界面的设置状态，比如候选路径相关参数。

- [data/cache/result_history.json](C:\Users\86182\Desktop\Algorithm\data\cache\result_history.json)
  作用：保存路径规划历史记录摘要，用于结果统计窗口。

- [data/cache/result_details/](C:\Users\86182\Desktop\Algorithm\data\cache\result_details)
  作用：保存每次规划结果的详情快照，例如图结构、路线点、调试信息等。

### 5.2 `data/processed/`

- [data/processed](C:\Users\86182\Desktop\Algorithm\data\processed)
  作用：预留给整理后的数据文件。当前核心水果配置已经迁移到了 `configs/fruit_profiles.json`。

---

## 6. 文档目录 `docs/`

这个目录主要是论文与展示素材，不是项目运行的必需代码。

- [docs/figures](C:\Users\86182\Desktop\Algorithm\docs\figures)
  作用：论文配图、算法图、流程图等图像资源。

- [docs/appendix_paper_assets](C:\Users\86182\Desktop\Algorithm\docs\appendix_paper_assets)
  作用：论文附录或参考资料抽取出来的图片素材。

- [docs/literature](C:\Users\86182\Desktop\Algorithm\docs\literature)
  作用：文献整理目录。

- [docs/reports](C:\Users\86182\Desktop\Algorithm\docs\reports)
  作用：报告或导出文档目录。

- [docs/result_detail_capture.png](C:\Users\86182\Desktop\Algorithm\docs\result_detail_capture.png)
  作用：结果详情界面截图素材。

---

## 7. 结果目录 `results/`

- [results/logs](C:\Users\86182\Desktop\Algorithm\results\logs)
  作用：程序运行日志目录。

- [results/logs/app.log](C:\Users\86182\Desktop\Algorithm\results\logs\app.log)
  作用：主应用日志。

- [results/metrics](C:\Users\86182\Desktop\Algorithm\results\metrics)
  作用：可用于存放算法运行指标、统计结果。

- [results/plots](C:\Users\86182\Desktop\Algorithm\results\plots)
  作用：可用于存放图表和可视化产物。

---

## 8. 源码目录 `src/`

## 8.1 包根

- [src/__init__.py](C:\Users\86182\Desktop\Algorithm\src\__init__.py)
  作用：将 `src` 目录声明为 Python 包。

### 8.2 `src/app/` 桌面应用层

- [src/app/__init__.py](C:\Users\86182\Desktop\Algorithm\src\app\__init__.py)
  作用：桌面应用包标记文件。

- [src/app/main.py](C:\Users\86182\Desktop\Algorithm\src\app\main.py)
  作用：桌面应用主入口。
  职责：
  - 创建 `QApplication`
  - 调用 `build_services()`
  - 创建主窗口控制器
  - 启动 Qt 事件循环

- [src/app/bootstrap.py](C:\Users\86182\Desktop\Algorithm\src\app\bootstrap.py)
  作用：应用装配入口，也可以理解为 composition root。
  职责：
  - 定位项目根目录
  - 读取配置
  - 初始化日志
  - 创建高德服务、地点联想服务、路径规划服务、智能助手服务
  - 将这些对象打包成 `AppServices`

- [src/app/config_loader.py](C:\Users\86182\Desktop\Algorithm\src\app\config_loader.py)
  作用：配置读取与解析模块。
  职责：
  - 读取 `app.yaml`
  - 合并 `secrets.yaml`
  - 将配置转换为 dataclass 风格对象
  - 提供默认策略映射和参数解析逻辑

- [src/app/controller.py](C:\Users\86182\Desktop\Algorithm\src\app\controller.py)
  作用：桌面主窗口控制器。
  职责：
  - 绑定 `.ui` 控件
  - 处理起点/终点输入
  - 加载与刷新策略选项
  - 发起路径规划
  - 更新地图、输出结果、运行日志
  - 管理历史结果、结果详情和智能助手交互
  这是当前桌面端最核心、最重的文件。

- [src/app/map_bridge.py](C:\Users\86182\Desktop\Algorithm\src\app\map_bridge.py)
  作用：Qt 与网页地图之间的桥接对象。
  职责：
  - 负责桌面端 Python 与 `ui/map.html` 之间的数据通信
  - 将路径结果传给网页地图进行渲染

### 8.3 `src/api/` HTTP 接口层

- [src/api/__init__.py](C:\Users\86182\Desktop\Algorithm\src\api\__init__.py)
  作用：API 包标记文件。

- [src/api/server.py](C:\Users\86182\Desktop\Algorithm\src\api\server.py)
  作用：FastAPI 主入口。
  职责：
  - 创建 `FastAPI` 应用
  - 暴露 `/health`、`/docs`、`/api/*` 接口
  - 复用 `bootstrap` 创建的业务服务
  - 返回内置网页页面 `/`、`/planner`、`/route-planner`

- [src/api/schemas.py](C:\Users\86182\Desktop\Algorithm\src\api\schemas.py)
  作用：API 请求/响应模型定义。
  职责：
  - 定义路径规划请求模型
  - 定义路径规划响应模型
  - 定义联想、聊天、健康检查、基础选项接口模型
  - 负责 API 模型与领域模型之间的转换

### 8.4 `src/services/` 业务服务层

- [src/services/__init__.py](C:\Users\86182\Desktop\Algorithm\src\services\__init__.py)
  作用：服务层包标记文件。

- [src/services/amap_web_service.py](C:\Users\86182\Desktop\Algorithm\src\services\amap_web_service.py)
  作用：高德 Web API 访问封装。
  职责：
  - 地理编码
  - 候选路线拉取
  - 路线折线解析
  - TMCS 路况解析
  - 缓存高德返回结果

- [src/services/place_suggestion_service.py](C:\Users\86182\Desktop\Algorithm\src\services\place_suggestion_service.py)
  作用：地点联想服务。
  职责：
  - 根据关键字返回地点候选项
  - 供 GUI 输入框和 API `/api/place/suggest` 使用

- [src/services/freshness_profile_loader.py](C:\Users\86182\Desktop\Algorithm\src\services\freshness_profile_loader.py)
  作用：水果保鲜配置读取器。
  职责：
  - 读取 `configs/fruit_profiles.json`
  - 生成可供业务层和算法层使用的水果参数对象

- [src/services/route_planning_service.py](C:\Users\86182\Desktop\Algorithm\src\services\route_planning_service.py)
  作用：路径规划核心业务服务。
  职责：
  - 统一接收 GUI / API 的规划请求
  - 调用高德获取路线或候选路径
  - 将候选路线构建为图
  - 调度 Dijkstra、A*、保鲜优化等算法
  - 计算里程、耗时、保鲜度、路径点等结果
  这是整个项目的业务核心文件。

### 8.5 `src/algorithms/` 算法实现层

- [src/algorithms/__init__.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\__init__.py)
  作用：算法包标记文件。

- [src/algorithms\dijkstra_shortest_path.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\dijkstra_shortest_path.py)
  作用：经典静态 Dijkstra 最短路径实现。

- [src/algorithms\time_dependent_shortest_path.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\time_dependent_shortest_path.py)
  作用：时变 Dijkstra 最短路径实现，支持动态时间权重。

- [src/algorithms\a_star_shortest_path.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\a_star_shortest_path.py)
  作用：A* 最短路径实现，使用启发式估价加速搜索。

- [src/algorithms\greedy_best_first_path.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\greedy_best_first_path.py)
  作用：贪心最佳优先搜索实现。

- [src/algorithms\tycoon_longest_route.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\tycoon_longest_route.py)
  作用：从候选路线中选择“最远/最长”的对照策略。

- [src/algorithms\freshness_dijkstra_improved.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\freshness_dijkstra_improved.py)
  作用：保鲜优先的 Dijkstra 改进算法。

- [src/algorithms\target_freshness_k_shortest_path.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\target_freshness_k_shortest_path.py)
  作用：目标保鲜 K 最短路算法（TF-KSP）。

- [src/algorithms\target_freshness_label_search.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\target_freshness_label_search.py)
  作用：目标保鲜偏差标签搜索算法（ATD-LS）。

- [src/algorithms\target_freshness_lexicographic_a_star.py](C:\Users\86182\Desktop\Algorithm\src\algorithms\target_freshness_lexicographic_a_star.py)
  作用：目标保鲜字典序 A* 算法（TF-LA*）。

### 8.6 `src/core/` 算法公共基础层

- [src/core/__init__.py](C:\Users\86182\Desktop\Algorithm\src\core\__init__.py)
  作用：公共基础层包标记文件。

- [src/core/graph.py](C:\Users\86182\Desktop\Algorithm\src\core\graph.py)
  作用：图结构定义文件。
  职责：
  - 定义 `GraphEdge`
  - 定义 `GraphData`
  - 提供 `build()`、`nearest_node()`、`haversine_km()` 等图结构基础能力

- [src/core/path_result.py](C:\Users\86182\Desktop\Algorithm\src\core\path_result.py)
  作用：算法统一结果模型。
  职责：
  - 定义 `PathSolveResult`
  - 作为各算法共享的标准返回结构

### 8.7 `src/models/` 领域模型层

- [src/models/__init__.py](C:\Users\86182\Desktop\Algorithm\src\models\__init__.py)
  作用：模型包标记文件。

- [src/models/route_request.py](C:\Users\86182\Desktop\Algorithm\src\models\route_request.py)
  作用：路径规划请求的领域模型。

- [src/models/route_result.py](C:\Users\86182\Desktop\Algorithm\src\models\route_result.py)
  作用：路径规划结果的领域模型。

- [src/models/map_payload.py](C:\Users\86182\Desktop\Algorithm\src\models\map_payload.py)
  作用：地图展示相关载荷模型，用于桌面地图或结果详情展示。

### 8.8 `src/utils/` 通用工具层

- [src/utils/__init__.py](C:\Users\86182\Desktop\Algorithm\src\utils\__init__.py)
  作用：工具包标记文件。

- [src/utils/coord_transform.py](C:\Users\86182\Desktop\Algorithm\src\utils\coord_transform.py)
  作用：坐标转换工具。
  职责：
  - `GCJ-02` 与 `WGS84` 相互转换
  - 批量坐标转换

- [src/utils/logger.py](C:\Users\86182\Desktop\Algorithm\src\utils\logger.py)
  作用：日志初始化与输出配置。

### 8.9 `src/agent/` 智能助手层

- [src/agent/__init__.py](C:\Users\86182\Desktop\Algorithm\src\agent\__init__.py)
  作用：智能助手包入口。

- [src/agent/mango_assistant_service.py](C:\Users\86182\Desktop\Algorithm\src\agent\mango_assistant_service.py)
  作用：芒小果智能助手服务。
  职责：
  - 调用外部大模型接口
  - 处理聊天上下文
  - 为 GUI 和 API 提供问答能力

---

## 9. 界面资源目录 `ui/`

- [ui/主界面.ui](C:\Users\86182\Desktop\Algorithm\ui\主界面.ui)
  作用：桌面主界面 `.ui` 文件。

- [ui/设置界面.ui](C:\Users\86182\Desktop\Algorithm\ui\设置界面.ui)
  作用：设置对话框界面。

- [ui/结果统计界面.ui](C:\Users\86182\Desktop\Algorithm\ui\结果统计界面.ui)
  作用：历史结果统计窗口界面。

- [ui/结果详情界面.ui](C:\Users\86182\Desktop\Algorithm\ui\结果详情界面.ui)
  作用：单次路径规划结果详情界面。

- [ui/map.html](C:\Users\86182\Desktop\Algorithm\ui\map.html)
  作用：桌面主界面内嵌地图页面。
  职责：
  - 加载高德地图
  - 接收 Python 传来的路径数据
  - 绘制起终点和路线

- [ui/result_detail_map.html](C:\Users\86182\Desktop\Algorithm\ui\result_detail_map.html)
  作用：结果详情窗口中展示路线与图结构的地图页面。

- [ui/assets/app_icon.svg](C:\Users\86182\Desktop\Algorithm\ui\assets\app_icon.svg)
  作用：应用图标资源。

---

## 10. 网页目录 `web/`

- [web/route_planner.html](C:\Users\86182\Desktop\Algorithm\web\route_planner.html)
  作用：浏览器端简单路径规划页面。
  职责：
  - 调用 `/api/meta/options` 获取选项
  - 调用 `/api/place/suggest` 做地点联想
  - 调用 `/api/route/plan` 发起规划
  - 使用高德 JS 地图渲染路线

---

## 11. 测试目录 `tests/`

### `tests/unit/`

- [tests/unit/test_api_server.py](C:\Users\86182\Desktop\Algorithm\tests\unit\test_api_server.py)
  作用：API 层单元测试。

- [tests/unit/test_target_freshness_atd_ls.py](C:\Users\86182\Desktop\Algorithm\tests\unit\test_target_freshness_atd_ls.py)
  作用：目标保鲜偏差标签搜索算法测试。

- [tests/unit/test_tf_ksp.py](C:\Users\86182\Desktop\Algorithm\tests\unit\test_tf_ksp.py)
  作用：目标保鲜 K 最短路算法测试。

---

## 12. 关键调用链

### 12.1 桌面端调用链

```text
main.py
  -> src.app.main.main()
  -> src.app.bootstrap.build_services()
  -> src.app.controller.MainWindowController
  -> src.services.route_planning_service.RoutePlanningService
  -> src.algorithms/*
```

### 12.2 API 调用链

```text
HTTP Request
  -> src.api.server
  -> src.api.schemas
  -> src.app.bootstrap.build_services()
  -> src.services.route_planning_service.RoutePlanningService
  -> src.algorithms/*
```

### 12.3 自研算法图构建链

```text
高德候选路线
  -> src.services.amap_web_service
  -> src.services.route_planning_service._build_graph_from_amap_candidates()
  -> src.core.graph.GraphData
  -> src.algorithms.*
```

---

## 13. 开发与维护建议

### 13.1 敏感信息管理

- 不要把真实 `configs/secrets.yaml` 提交到仓库
- 建议在部署前使用环境变量或服务器本地文件管理密钥
- 如果敏感 key 曾经暴露，应立即轮换

### 13.2 运行时目录

下面这些内容通常不建议直接纳入正式代码维护范围：

- `.venv/`
- `.idea/`
- `__pycache__/`
- `data/cache/`
- `results/logs/`
- 临时 `uvicorn_*.log`

### 13.3 修改后常见启动方式

桌面端：

```powershell
.\.venv\Scripts\python.exe .\main.py
```

本地 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload
```

服务器更新：

```bash
cd /opt/algorithm
git pull
systemctl restart algorithm-api
systemctl restart nginx
```

---

## 14. 当前项目定位

这个项目不是一个单纯的算法练习仓库，而是一个综合型研究平台。它同时包含：

- 基于高德候选路径的二次规划搜索
- 多种经典与自研路径算法
- 水果冷链运输保鲜模型
- 桌面 GUI 展示
- HTTP API 封装
- 浏览器端简易演示页
- 论文与展示素材

如果从维护角度看，当前最重要的核心文件主要是：

- [src/app/controller.py](C:\Users\86182\Desktop\Algorithm\src\app\controller.py)
- [src/app/bootstrap.py](C:\Users\86182\Desktop\Algorithm\src\app\bootstrap.py)
- [src/app/config_loader.py](C:\Users\86182\Desktop\Algorithm\src\app\config_loader.py)
- [src/api/server.py](C:\Users\86182\Desktop\Algorithm\src\api\server.py)
- [src/services/route_planning_service.py](C:\Users\86182\Desktop\Algorithm\src\services\route_planning_service.py)
- [src/services/amap_web_service.py](C:\Users\86182\Desktop\Algorithm\src\services\amap_web_service.py)
- [src/core/graph.py](C:\Users\86182\Desktop\Algorithm\src\core\graph.py)
- [src/core/path_result.py](C:\Users\86182\Desktop\Algorithm\src\core\path_result.py)

---

## 15. 一句话总结

这是一个围绕“水果运输路径规划与保鲜优化”构建的综合研究平台，核心由 `服务层 + 算法层 + 图结构层` 组成，外层同时提供桌面 GUI、HTTP API 和简易网页入口。
