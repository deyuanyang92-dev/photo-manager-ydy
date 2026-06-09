"""global_hotkey.py — system-wide screenshot hotkey via pynput (optional dep).

The in-app QShortcut (ApplicationShortcut) only fires while one of the app's
windows has focus.  To let the user press the screenshot key from *anywhere*
(app minimised / another program in front), we additionally register a global
OS-level hotkey through ``pynput.keyboard.GlobalHotKeys``.

``pynput`` is an OPTIONAL runtime dependency — same convention as cjxl/Helicon:
absent → ``available()`` is False and the manager degrades to a no-op (the
in-app shortcut still works).  Platform caveats: X11/Windows/macOS work
(macOS needs Accessibility permission); Wayland blocks global key grabbing, so
registration may silently do nothing there.

The pynput listener runs on its OWN thread, so the hotkey callback re-emits a
Qt signal (``triggered``) which callers connect with a queued connection to a
main-thread slot — never touch widgets from the listener thread.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


def available() -> bool:
    """True when pynput is importable (global hotkeys can be registered)."""
    try:
        import pynput  # noqa: F401
        return True
    except Exception:
        return False


# Qt modifier/key token → pynput GlobalHotKeys token.
_MOD_MAP = {
    "ctrl": "<ctrl>", "control": "<ctrl>",
    "alt": "<alt>", "option": "<alt>", "opt": "<alt>",
    "shift": "<shift>",
    "meta": "<cmd>", "cmd": "<cmd>", "command": "<cmd>",
    "super": "<cmd>", "win": "<cmd>",
}


def qt_seq_to_pynput(seq: str) -> Optional[str]:
    """Convert a Qt key sequence ('Alt+A', 'Ctrl+Shift+S') to pynput form.

    Returns None when the sequence is empty or has no single trigger key, in
    which case no global hotkey is registered.
    """
    if not seq:
        return None
    parts = [p for p in seq.replace(" ", "").split("+") if p]
    if not parts:
        return None
    *mods, key = parts
    out = []
    for m in mods:
        token = _MOD_MAP.get(m.lower())
        if token is None:
            return None  # unknown modifier — bail rather than mis-bind
        out.append(token)
    k = key.lower()
    if len(k) == 1:
        out.append(k)
    else:
        # Function / named keys (f1, esc, space…) use angle-bracket form.
        out.append(f"<{k}>")
    return "+".join(out)


class GlobalHotkeyManager(QObject):
    """Registers ONE global hotkey and re-emits it as a Qt signal."""

    triggered = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._listener = None

    def set_hotkey(self, qt_seq: str) -> bool:
        """(Re)register the global hotkey from a Qt key sequence string.

        Returns True if a system-wide listener is now active for it.  Any
        failure (pynput missing, Wayland, bad combo) degrades to False without
        raising — the in-app shortcut remains the fallback.
        """
        self.stop()
        combo = qt_seq_to_pynput(qt_seq)
        if not combo:
            return False
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({combo: self._on_fire})
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:
            self._listener = None
            return False

    def _on_fire(self) -> None:
        # Runs on the pynput listener thread — only emit (queued → main thread).
        self.triggered.emit()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
