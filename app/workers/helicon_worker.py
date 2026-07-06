"""helicon_worker.py — QThread for non-blocking Helicon Focus execution."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


class HeliconWorker(QThread):
    finished = pyqtSignal(object) # Path of output TIFF
    failed = pyqtSignal(str)      # error message

    def __init__(self, cmd: list, output_path, parent=None) -> None:
        super().__init__(parent)
        self._cmd = cmd
        self._output_path = Path(output_path) if output_path else Path()
        self._proc = None
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self.quit()

    _TIMEOUT_S = 600  # 10 min — mirrors oracle stackSingle timeout

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = self._proc.communicate(timeout=self._TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.communicate()
                message = (
                    "用户取消"
                    if self._cancel_requested
                    else "Helicon 进程超时（10 分钟）"
                )
                self.failed.emit(message)
                return
            if self._cancel_requested:
                return
            if self._proc.returncode != 0:
                self.failed.emit(stderr.decode(errors="replace") or "Helicon 返回错误")
                return
            if not self._output_path.exists():
                self.failed.emit("输出文件未生成")
                return
            self.finished.emit(self._output_path)
        except Exception as exc:
            self.failed.emit(str(exc))
