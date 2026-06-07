"""桌面应用主入口。

本文件负责创建 Qt 应用实例、构建应用运行所需的服务对象，
并启动主窗口控制器与事件循环，是桌面 GUI 模式的顶层启动点。
"""


from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from src.app.bootstrap import build_services
from src.app.controller import MainWindowController


LOGGER = logging.getLogger(__name__)


# 构建服务对象并启动桌面界面事件循环。
def main() -> int:
    services = build_services()

    app = QApplication(sys.argv)
    controller = MainWindowController(
        project_root=services.project_root,
        app_config=services.app_config,
        route_service=services.route_service,
        place_suggestion_service=services.place_suggestion_service,
        mango_assistant_service=services.mango_assistant_service,
    )
    app.aboutToQuit.connect(controller.shutdown)
    controller.window.show()

    LOGGER.info("应用启动完成")
    return app.exec()
