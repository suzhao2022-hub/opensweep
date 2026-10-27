"""`OpenSweep --guard`: the once-at-logon startup check.

Runs without a window: switches back off whatever sneaked back into startup,
shows one notification if anything happened, and exits. Clicking the
notification opens OpenSweep on the Startup page.
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from opensweep.core import guard
from opensweep.core.platform import is_admin
from opensweep.i18n import detect, set_language, t
from opensweep.settings import Settings


def _open_app() -> None:
    if getattr(sys, "frozen", False):
        subprocess.Popen([sys.executable, "--page", "startup"])
    else:  # development checkout: make the package importable for the child
        src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        subprocess.Popen([sys.executable, "-m", "opensweep", "--page", "startup"],
                         env={**os.environ, "PYTHONPATH": src})


def main() -> int:
    settings = Settings.load()
    if not settings.guard:
        return 0
    try:
        result = guard.enforce(admin=is_admin())
    except Exception as exc:  # never bother the user at logon because of our own bug
        from opensweep.gui.errors import log_error
        log_error(f"guard: {exc!r}")
        return 1
    if not result.fixed and not result.need_admin:
        return 0

    set_language(settings.language or detect())
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    from opensweep.gui.widgets import app_icon
    tray = QSystemTrayIcon(app_icon())
    tray.setToolTip("OpenSweep")
    tray.show()
    if result.fixed:
        names = ", ".join(e.display_name for e in result.fixed[:3]) + ("…" if len(result.fixed) > 3 else "")
        title = t("guard.notice_title", count=len(result.fixed))
        text = t("guard.notice_fixed", names=names)
    else:
        names = ", ".join(e.display_name for e in result.need_admin[:3])
        title = t("guard.notice_admin_title", count=len(result.need_admin))
        text = t("guard.notice_admin", names=names)
    tray.messageClicked.connect(lambda: (_open_app(), app.quit()))
    tray.showMessage(title, text, app_icon(), 10000)
    QTimer.singleShot(15000, app.quit)
    return app.exec()
