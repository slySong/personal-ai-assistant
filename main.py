"""GUI 入口。启动桌面应用。"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from config import AppConfig, LLMConfig
from gui.main_window import MainWindow, apply_global_theme


def main() -> int:
    # 高 DPI 支持（PySide6 默认开启，显式设置更稳）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.HighDpiScaleFactorRoundingPolicy
        .PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("私人 AI 助手")
    app.setOrganizationName("PersonalAIAssistant")

    # 加载配置
    app_cfg = AppConfig.load()
    llm_cfg = LLMConfig(
        base_url=app_cfg.llm.base_url,
        api_key=app_cfg.llm.api_key,
        model=app_cfg.llm.model,
        temperature=app_cfg.llm.temperature,
        max_tokens=app_cfg.llm.max_tokens,
        timeout=app_cfg.llm.timeout,
        request_timeout=app_cfg.llm.request_timeout,
    )

    # 应用全局主题（深色/浅色）
    apply_global_theme(app, app_cfg.theme)

    window = MainWindow(app_cfg, llm_cfg)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
