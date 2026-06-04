"""helicon_worker.py — QThread for non-blocking Helicon Focus execution."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


class HeliconWorker(QThread):
    progress = pyqtSignal(int)    # percent 0-100
    finished = pyqtSignal(object) # Path of output TIFF
    failed = pyqtSignal(str)      # error message

    def __init__(self, cmd: list, output_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._cmd = cmd
        self._output_path = output_path
        self._proc = None

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self.quit()

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = self._proc.communicate()
            if self._proc.returncode != 0:
                self.failed.emit(stderr.decode(errors="replace") or "Helicon 返回错误")
                return
            if not self._output_path.exists():
                self.failed.emit("输出文件未生成")
                return
            self.finished.emit(self._output_path)
        except Exception as exc:
            self.failed.emit(str(exc))
