from PyQt6.QtWidgets import QDialog

from app.services.label_print_executor import LabelPrintExecutor


class _WindowsUnavailable:
    @staticmethod
    def is_available():
        return False


class _FakePrinter:
    def __init__(self):
        self.name = ""

    def setPrinterName(self, name):
        self.name = name

    def printerName(self):
        return self.name


def _job(bucket="sample", n=1, paper_type="a4"):
    return {
        "bucket": bucket,
        "items": [{"idx": i, "data": {"uniqueId": f"U{i}"}} for i in range(n)],
        "labels": [{"uniqueId": f"U{i}"} for i in range(n)],
        "paperType": paper_type,
        "gridOpts": {"orientation": "landscape"},
    }


def test_executor_dialog_path_builds_printer_and_paints_jobs():
    captured = {}

    class Dialog:
        def __init__(self, jobs, parent=None):
            captured["dialog_jobs"] = jobs

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_printer(self):
            return "Printer A"

    def build(job, grid_opts=None):
        captured["build_job"] = job
        captured["build_grid_opts"] = grid_opts
        return _FakePrinter()

    def paint(printer, jobs, **kw):
        captured["printer_name"] = printer.printerName()
        captured["paint_jobs"] = jobs
        captured["paint_kw"] = kw
        return True

    result = LabelPrintExecutor(
        dialog_cls=Dialog,
        build_printer_fn=build,
        paint_jobs_fn=paint,
        windows_print_module=_WindowsUnavailable,
    ).print_with_dialog([_job()], grid_opts={"forceCols": 2}, cut_marks=True)

    assert result.printed is True
    assert result.printer_name == "Printer A"
    assert captured["build_grid_opts"] == {"orientation": "landscape"}
    assert captured["printer_name"] == "Printer A"
    assert captured["paint_kw"]["grid_opts"] == {"forceCols": 2}
    assert captured["paint_kw"]["cut_marks"] is True


def test_executor_dialog_cancel_returns_not_accepted():
    class Dialog:
        def __init__(self, jobs, parent=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    result = LabelPrintExecutor(
        dialog_cls=Dialog,
        windows_print_module=_WindowsUnavailable,
    ).print_with_dialog([_job()])

    assert result.printed is False
    assert result.accepted is False


def test_executor_windows_path_uses_windows_bridge():
    captured = {}

    class WindowsAvailable:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def print_jobs_with_windows_dialog(jobs, **kw):
            captured["jobs"] = jobs
            captured["kw"] = kw
            return True, "Windows Printer"

    result = LabelPrintExecutor(
        windows_print_module=WindowsAvailable,
    ).print_with_dialog([_job()], document_name="Doc", cut_marks=True)

    assert result.printed is True
    assert result.printer_name == "Windows Printer"
    assert captured["kw"]["document_name"] == "Doc"
    assert captured["kw"]["cut_marks"] is True


def test_executor_windows_direct_default_printer_has_display_name():
    captured = {}

    class WindowsAvailable:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def print_jobs_with_windows_dialog(jobs, **kw):
            captured["kw"] = kw
            return True, ""

    result = LabelPrintExecutor(
        windows_print_module=WindowsAvailable,
    ).print_direct([_job()], printer_name="")

    assert result.printed is True
    assert result.printer_name == "Windows 打印机"
    assert captured["kw"]["printer_name"] == ""
    assert captured["kw"]["show_dialog"] is False


def test_executor_windows_error_reports_message():
    captured = {}

    class WindowsBroken:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def print_jobs_with_windows_dialog(jobs, **kw):
            raise RuntimeError("spool failed")

    class Messages:
        @staticmethod
        def critical(parent, title, text):
            captured["title"] = title
            captured["text"] = text

    result = LabelPrintExecutor(
        windows_print_module=WindowsBroken,
        message_box=Messages,
    ).print_with_dialog([_job()])

    assert result.printed is False
    assert result.error == "spool failed"
    assert captured == {"title": "打印失败", "text": "spool failed"}


def test_executor_direct_path_prints_without_dialog():
    captured = {}

    def build(job, grid_opts=None):
        captured["build_grid_opts"] = grid_opts
        return _FakePrinter()

    def paint(printer, jobs, **kw):
        captured["printer_name"] = printer.printerName()
        captured["jobs"] = jobs
        return True

    result = LabelPrintExecutor(
        build_printer_fn=build,
        paint_jobs_fn=paint,
        windows_print_module=_WindowsUnavailable,
    ).print_direct([_job()], printer_name="Printer B")

    assert result.printed is True
    assert result.printer_name == "Printer B"
    assert captured["printer_name"] == "Printer B"
    assert captured["build_grid_opts"] == {"orientation": "landscape"}


def test_executor_direct_path_empty_printer_means_system_default():
    captured = {}

    def paint(printer, jobs, **kw):
        captured["printer_name"] = printer.printerName()
        return True

    result = LabelPrintExecutor(
        build_printer_fn=lambda job, grid_opts=None: _FakePrinter(),
        paint_jobs_fn=paint,
        windows_print_module=_WindowsUnavailable,
    ).print_direct([_job()], printer_name="")

    assert result.printed is True
    assert result.printer_name == "default"
    assert captured["printer_name"] == ""


def test_executor_direct_niimbot_path_bypasses_qt_and_windows(monkeypatch):
    from app.services import niimbot_print_service as niimbot

    captured = {}

    def fake_print(jobs, **kw):
        captured["jobs"] = jobs
        captured["kw"] = kw
        return "NIIMBOT B203 USB (COM5)"

    def build_should_not_run(job, grid_opts=None):
        raise AssertionError("Qt printer path should not run for NIIMBOT")

    monkeypatch.setattr(niimbot, "print_jobs_to_niimbot", fake_print)

    result = LabelPrintExecutor(
        build_printer_fn=build_should_not_run,
        windows_print_module=_WindowsUnavailable,
    ).print_direct([_job(paper_type="label")], printer_name=niimbot.printer_id("COM5"))

    assert result.printed is True
    assert result.printer_name == "NIIMBOT B203 USB (COM5)"
    assert captured["kw"]["printer_name"] == "NIIMBOT:B203:COM5"
    assert captured["kw"]["document_name"]


def test_executor_dialog_niimbot_path_bypasses_windows_bridge(monkeypatch):
    from app.services import niimbot_print_service as niimbot

    captured = {}

    class WindowsAvailable:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def print_jobs_with_windows_dialog(jobs, **kw):
            raise AssertionError("Windows bridge should not handle NIIMBOT")

    class Dialog:
        def __init__(self, jobs, parent=None):
            captured["dialog_jobs"] = jobs

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_printer(self):
            return niimbot.printer_id("COM5")

    monkeypatch.setattr(
        niimbot,
        "available_printers",
        lambda: [niimbot.NiimbotPrinter(
            id=niimbot.printer_id("COM5"),
            name="NIIMBOT B203 USB (COM5)",
            port="COM5",
        )],
    )
    def fake_niimbot_print(jobs, **kw):
        captured["niimbot_jobs"] = jobs
        captured["niimbot_kw"] = kw
        return "NIIMBOT B203 USB (COM5)"

    monkeypatch.setattr(niimbot, "print_jobs_to_niimbot", fake_niimbot_print)

    result = LabelPrintExecutor(
        dialog_cls=Dialog,
        windows_print_module=WindowsAvailable,
    ).print_with_dialog([_job(paper_type="label")])

    assert result.printed is True
    assert result.printer_name == "NIIMBOT B203 USB (COM5)"
    assert captured["niimbot_kw"]["printer_name"] == "NIIMBOT:B203:COM5"
