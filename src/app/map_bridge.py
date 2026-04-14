from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal, Slot

from src.models.map_payload import MapPayload


class MapBridge(QObject):
    routeDataChanged = Signal(str)
    mapReady = Signal()
    jsLog = Signal(str)

    @Slot()
    def notifyMapReady(self) -> None:
        self.mapReady.emit()

    @Slot(str)
    def logMessage(self, message: str) -> None:
        self.jsLog.emit(message)

    def send_payload(self, payload: MapPayload | dict[str, object]) -> None:
        """将路径数据发送给前端地图。"""
        raw = asdict(payload) if isinstance(payload, MapPayload) else payload
        self.routeDataChanged.emit(json.dumps(raw, ensure_ascii=False))
