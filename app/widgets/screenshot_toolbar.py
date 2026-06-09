"""screenshot_toolbar.py — floating action/annotation toolbar for the editor.

A compact frameless strip the overlay positions next to the selection. Left
group = annotation tools (exclusive, checkable) + colour swatches + stroke
width; right group = undo / copy / save / pin / done / cancel actions. Pure
signals — the overlay owns all state and pixels.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QWidget,
)

from app.config import icons
from app.widgets.screenshot_annotations import Tool

_TOOLS = [
    (Tool.RECT, "mdi6.rectangle-outline", "矩形"),
    (Tool.ARROW, "mdi6.arrow-top-right", "箭头"),
    (Tool.PEN, "mdi6.pencil", "画笔"),
    (Tool.TEXT, "mdi6.format-text", "文字"),
    (Tool.HIGHLIGHT, "mdi6.marker", "高亮"),
    (Tool.MOSAIC, "mdi6.blur", "马赛克"),
]

_SWATCHES = ["#FF4040", "#FFC400", "#22C55E", "#3B82F6", "#111111", "#FFFFFF"]


class ScreenshotToolbar(QFrame):
    toolChanged = pyqtSignal(object)      # Tool | None
    colorChanged = pyqtSignal(QColor)
    widthChanged = pyqtSignal(int)
    undoRequested = pyqtSignal()
    copyRequested = pyqtSignal()
    saveRequested = pyqtSignal()
    pinRequested = pyqtSignal()
    doneRequested = pyqtSignal()
    cancelRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ShotToolbar")
        self.setStyleSheet(
            "#ShotToolbar{background:#1e1e1e;border:1px solid #3a3a3a;"
            "border-radius:8px;}"
            "#ShotToolbar QPushButton{border:none;border-radius:5px;padding:4px;"
            "background:transparent;}"
            "#ShotToolbar QPushButton:hover{background:#333;}"
            "#ShotToolbar QPushButton:checked{background:#0a84ff;}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for tool, glyph, tip in _TOOLS:
            b = self._icon_button(glyph, tip)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, t=tool, btn=b: self._on_tool(t, btn))
            self._tool_group.addButton(b)
            lay.addWidget(b)

        lay.addWidget(_sep())

        for hexc in _SWATCHES:
            sw = QPushButton()
            sw.setFixedSize(18, 18)
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.setStyleSheet(
                f"background:{hexc};border:1px solid #555;border-radius:9px;"
            )
            sw.clicked.connect(lambda _=False, c=hexc: self.colorChanged.emit(QColor(c)))
            lay.addWidget(sw)

        self._width = QSpinBox()
        self._width.setRange(1, 24)
        self._width.setValue(3)
        self._width.setFixedWidth(46)
        self._width.setStyleSheet("color:#eee;background:#2a2a2a;border:none;")
        self._width.valueChanged.connect(self.widthChanged)
        lay.addWidget(self._width)

        lay.addWidget(_sep())

        self._add_action(lay, "mdi6.undo", "撤销", self.undoRequested)
        self._add_action(lay, "mdi6.content-copy", "复制到剪贴板", self.copyRequested)
        self._add_action(lay, "mdi6.content-save-outline", "保存…", self.saveRequested)
        self._add_action(lay, "mdi6.pin", "钉到桌面", self.pinRequested)
        self._add_action(lay, "mdi6.close", "取消", self.cancelRequested)
        done = self._add_action(lay, "mdi6.check", "完成", self.doneRequested)
        done.setStyleSheet("background:#22C55E;border-radius:5px;")

    # ── helpers ──────────────────────────────────────────────────────────
    def _icon_button(self, glyph: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(28, 28)
        b.setIcon(icons.icon(glyph, color="#dddddd", color_active="#ffffff"))
        b.setIconSize(QSize(17, 17))
        return b

    def _add_action(self, lay, glyph, tip, signal) -> QPushButton:
        b = self._icon_button(glyph, tip)
        b.clicked.connect(lambda: signal.emit())
        lay.addWidget(b)
        return b

    def _on_tool(self, tool: Tool, btn: QPushButton) -> None:
        # Clicking the checked tool again toggles back to "no tool" (cursor).
        if getattr(self, "_active_tool", None) is tool:
            self._tool_group.setExclusive(False)
            btn.setChecked(False)
            self._tool_group.setExclusive(True)
            self._active_tool = None
            self.toolChanged.emit(None)
        else:
            self._active_tool = tool
            self.toolChanged.emit(tool)


def _sep() -> QFrame:
    s = QFrame()
    s.setFrameShape(QFrame.Shape.VLine)
    s.setStyleSheet("color:#3a3a3a;")
    s.setFixedWidth(8)
    return s
