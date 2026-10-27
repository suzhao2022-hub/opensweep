"""Make every unexpected error visible: a message box for the user and a line in the log.

Without this, an exception inside a button handler only goes to stderr, which a
windowed app doesn't have, so the button simply seems to do nothing.
"""

from __future__ import annotations

import sys
import time
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from opensweep.core.platform import log_dir

_showing = False


def log_error(text: str) -> None:
    try:
        with open(log_dir() / "errors.log", "a", encoding="utf-8") as fh:
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
    except OSError:
        pass


def show_error(message: str, details: str = "") -> None:
    global _showing
    log_error(details or message)
    if _showing or QApplication.instance() is None:
        return
    from opensweep.i18n import t
    _showing = True
    try:
        box = QMessageBox(QApplication.activeWindow())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(t("common.error"))
        box.setText(t("errors.unexpected") + "\n\n" + message.splitlines()[-1][:300])
        if details:
            box.setDetailedText(details)
        box.exec()
    finally:
        _showing = False


def install() -> None:
    def hook(exc_type, exc, tb):
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.__stderr__ and sys.__stderr__.write(details)
        show_error(f"{exc_type.__name__}: {exc}", details)
    sys.excepthook = hook
