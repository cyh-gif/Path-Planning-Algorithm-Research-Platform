from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from src.app.bootstrap import build_services
from src.app.controller import MainWindowController


LOGGER = logging.getLogger(__name__)


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
