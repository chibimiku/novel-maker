"""pytest 全局配置：让 Qt 在无显示环境下运行。"""
from __future__ import annotations

import os

# 必须在任何 PyQt6 导入之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    """整个测试会话共享一个 QApplication。"""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
