"""地图桥接模块。

本文件封装 QWebChannel 所需的桥接对象，用于在 Qt 控制器
与嵌入式地图页面之间双向传递就绪事件、日志消息和路径载荷数据，
是桌面地图联动的关键通信层。
"""


from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal, Slot

from src.models.map_payload import MapPayload


# 封装 QWebChannel 双向通信所需的桥接对象。
class MapBridge(QObject):
    routeDataChanged = Signal(str)
    mapReady = Signal()
    jsLog = Signal(str)

    @Slot()
    # 接收前端地图就绪通知并向控制器转发信号。
    def notifyMapReady(self) -> None:
        self.mapReady.emit()

    @Slot(str)
    # 接收前端日志消息并转发给控制器记录。
    def logMessage(self, message: str) -> None:
        self.jsLog.emit(message)

    # 将地图展示载荷序列化后发送给前端页面。
    def send_payload(self, payload: MapPayload | dict[str, object]) -> None:
        """将路径数据发送给前端地图。"""
        raw = asdict(payload) if isinstance(payload, MapPayload) else payload
        self.routeDataChanged.emit(json.dumps(raw, ensure_ascii=False))
