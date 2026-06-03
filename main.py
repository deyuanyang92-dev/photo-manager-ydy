"""标本照片工作台 — 桌面版入口。

W0 骨架：能启动一个空主窗口。后续 W1 接入工作台视图。
"""
import sys

from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel
from PyQt6.QtCore import Qt


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("标本照片工作台")
    app.setOrganizationName("SpecimenPhotoWorkbench")

    win = QMainWindow()
    win.setWindowTitle("标本照片工作台")
    win.resize(1280, 800)
    placeholder = QLabel("W0 骨架就绪 · 拍照工作台即将接入")
    placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    win.setCentralWidget(placeholder)
    win.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
