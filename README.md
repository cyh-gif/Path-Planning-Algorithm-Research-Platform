# 水果运输最短路径研究系统（PySide6 + 高德 + 自研算法）

## 项目简介
本项目用于研究水果运输场景中的最短路径规划，并提供可视化交互界面。

- 左侧输入参数：起点、终点、路径策略、水果类型、运输方式等
- 中间地图：高德在线底图 + 路径叠加渲染
- 右侧输出结果：总里程、总时长、计算耗时、路径点数等

当前版本采用**统一高德数据链路**：

- 高德策略项：直接调用高德驾车规划 API
- 自研算法项：仍使用 `src/algorithms`，但图数据由高德候选路径在线构建，不再读取本地路径点文件

## 目录说明
- `ui/`：界面与地图渲染资源（`.ui` + `map.html`）
- `src/app/`：启动、配置、界面控制、Qt-JS 桥接
- `src/services/`：高德 API 封装、路径规划服务、地点联想服务
- `src/algorithms/`：自研图搜索算法（静态Dijkstra、时变Dijkstra、A*）
- `src/models/`：请求/响应与地图载荷模型
- `src/utils/`：坐标转换与日志工具
- `tests/`：单元/集成/UI 协议测试

## 配置说明
主配置文件：`configs/app.yaml`

关键字段：
- `amap.js_key`：高德 JS API Key（底图加载）
- `amap.security_js_code`：高德安全密钥（JS 安全配置）
- `amap.web_service_key`：高德 Web Service Key（地理编码/路径规划）
- `routing.amap_strategy_map`：高德策略映射
- `routing.custom_algorithm_map`：自研算法映射
- `custom_time_dependent.peak_hours_csv`：高峰时段
- `custom_time_dependent.peak_multiplier`：高峰倍率

环境变量优先级高于配置文件，可使用：
- `AMAP_JS_KEY`
- `AMAP_SECURITY_JS_CODE`
- `AMAP_WEB_SERVICE_KEY`

## 运行方式
```bash
pip install -r requirements.txt
python main.py
```

## 测试方式
```bash
python -m unittest discover -s tests
```
