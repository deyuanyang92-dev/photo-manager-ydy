"""capture_settings_func.py — Capture SettingsView Helicon tab with preset populated."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys

# Must be set before QApplication is created
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from app.app_context import AppContext
from app.views.settings_view import SettingsView

app = QApplication(sys.argv)
ctx = AppContext()
# Clear any leftover settings from prior runs
ctx.settings._qs.clear()

view = SettingsView(ctx)
view.resize(1920, 1080)
view.on_activate()
view.show()

# Switch to Helicon tab (index 1) and populate a preset
view._tabs.setCurrentIndex(1)
view._preset_name_edit.setText("标准景深叠加")
view._method_combo.setCurrentIndex(1)   # B — 景深图
view._radius_spin.setValue(4)
view._smoothing_spin.setValue(4)
view._quality_spin.setValue(95)
view._save_current_as_preset()

# Add a second preset to show the list
view._preset_name_edit.setText("精细模式")
view._method_combo.setCurrentIndex(2)   # C — 金字塔
view._radius_spin.setValue(8)
view._smoothing_spin.setValue(2)
view._quality_spin.setValue(90)
view._save_current_as_preset()

# Select first item to show apply state
view._preset_list.setCurrentRow(0)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_func.png")

def do_shot():
    pix = view.grab()
    pix.save(OUT)
    print(f"saved {OUT}")
    app.quit()

QTimer.singleShot(300, do_shot)
sys.exit(app.exec())
