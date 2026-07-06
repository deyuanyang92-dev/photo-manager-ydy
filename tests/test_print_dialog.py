from app.widgets.print_dialog import PrintJobDialog


def test_print_dialog_shows_niimbot_media_warning(qtbot, monkeypatch):
    from app.services import niimbot_print_service as niimbot

    monkeypatch.setattr(
        niimbot,
        "available_printers",
        lambda: [niimbot.NiimbotPrinter(
            id=niimbot.printer_id("COM5"),
            name="NIIMBOT B203 USB (COM5)",
            port="COM5",
        )],
    )
    jobs = [{
        "bucket": "sample",
        "items": [{"idx": 0, "data": {"uniqueId": "U1"}}],
        "paperType": "a4",
    }]

    dlg = PrintJobDialog(jobs)
    qtbot.addWidget(dlg)
    dlg._refresh_printer_combo(niimbot.printer_id("COM5"))
    dlg._sync_niimbot_hint()

    assert not dlg._niimbot_hint.isHidden()
    assert "T40×30mm" in dlg._niimbot_hint.text()
    assert "A4/A5" in dlg._niimbot_hint.text()


def test_print_dialog_lists_windows_bridge_printers(qtbot, monkeypatch):
    from app.utils import windows_print

    monkeypatch.setattr(windows_print, "is_available", lambda: True)
    monkeypatch.setattr(windows_print, "windows_printer_names", lambda: ["Win Printer"])

    dlg = PrintJobDialog([{
        "bucket": "sample",
        "items": [{"idx": 0, "data": {"uniqueId": "U1"}}],
        "paperType": "label",
    }])
    qtbot.addWidget(dlg)
    dlg._refresh_printer_combo("Win Printer")

    assert dlg._printer_combo.findData("Win Printer") >= 0
    assert dlg._printer_combo.currentData() == "Win Printer"
