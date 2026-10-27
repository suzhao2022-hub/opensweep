"""Main window and application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from opensweep import APP_NAME, __version__
from opensweep.core.platform import IS_WINDOWS, is_admin
from opensweep.core.rules import load_rules
from opensweep.gui import errors, theme
from opensweep.gui.pages.clean import CleanPage
from opensweep.gui.pages.disk import DiskPage
from opensweep.gui.pages.home import AI, OTHER, HomePage
from opensweep.gui.pages.memory import MemoryPage
from opensweep.gui.pages.settings import SettingsPage
from opensweep.gui.pages.software import SoftwarePage
from opensweep.gui.pages.startup import StartupPage
from opensweep.gui.store import ScanStore
from opensweep.gui.workers import run_task
from opensweep.gui.widgets import app_icon, draw_logo, label
from opensweep.i18n import detect, set_language, t
from opensweep.settings import Settings

NAV_ICONS = {"home": "home", "clean": "clean", "ai": "ai", "disk": "disk", "software": "software",
             "startup": "startup", "memory": "memory", "settings": "settings"}


class MainWindow(QMainWindow):
    theme_changed = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        rules, rule_errors = load_rules()
        self.store = ScanStore(rules)
        admin = IS_WINDOWS and is_admin()
        self.setWindowTitle(APP_NAME + (f" ({t('app.admin')})" if admin else ""))
        self.setWindowIcon(app_icon())
        self.resize(1240, 800)
        self.setMinimumSize(980, 640)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 20, 0, 16)
        side_lay.setSpacing(0)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(22, 0, 16, 0)
        brand_row.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(draw_logo(64).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_row.addWidget(logo)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand_col.addWidget(label(APP_NAME, "brand"))
        brand_col.addWidget(label(t("app.tagline"), "brandSub"))
        brand_row.addLayout(brand_col, 1)
        side_lay.addLayout(brand_row)
        side_lay.addSpacing(22)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setCursor(Qt.PointingHandCursor)
        side_lay.addWidget(self.nav, 1)
        foot = label(f"v{__version__}" + (f"  ·  {t('app.admin')}" if admin else ""), "faint")
        foot.setContentsMargins(24, 0, 0, 0)
        side_lay.addWidget(foot)
        root.addWidget(side)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        home = HomePage(self.store, settings)
        home.open_page.connect(self.show_page)
        self.pages: dict[str, QWidget] = {
            "home": home,
            "clean": CleanPage(self.store, settings, OTHER, t("clean.title"), t("clean.desc")),
            "ai": CleanPage(self.store, settings, AI, t("ai.title"), t("ai.desc")),
            "disk": DiskPage(),
        }
        self.pages["disk"].open_page.connect(self.show_page)
        if IS_WINDOWS:
            self.pages["software"] = SoftwarePage()
            self.pages["startup"] = StartupPage(settings)
        self.pages["memory"] = MemoryPage()
        self.pages["settings"] = SettingsPage(settings, rule_errors)

        memory, disk = self.pages["memory"], self.pages["disk"]
        disk.changed.connect(home.mark_stale)
        if "startup" in self.pages:
            startup_page, software = self.pages["startup"], self.pages["software"]
            # Whoever changes startup entries, the others re-read them when next shown.
            startup_page.changed.connect(home.mark_stale)
            startup_page.changed.connect(memory.mark_stale)
            memory.startup_changed.connect(home.mark_stale)
            memory.startup_changed.connect(startup_page.mark_stale)
            home.startup_changed.connect(startup_page.mark_stale)
            home.startup_changed.connect(memory.mark_stale)
            software.changed.connect(home.mark_stale)
            software.changed.connect(startup_page.mark_stale)
            software.changed.connect(memory.mark_stale)
        self.pages["settings"].theme_changed.connect(self.theme_changed)
        for key, page in self.pages.items():
            item = QListWidgetItem(theme.icon(NAV_ICONS[key], selected_color=theme.nav_selected_text()),
                                   "  " + t(f"nav.{key}"))
            item.setData(Qt.UserRole, key)
            item.setSizeHint(QSize(200, 42))
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

    def show_page(self, key: str, focus: str = "") -> None:
        """Switch page; `focus` is page specific (a rule id, a filter...)."""
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.UserRole) == key:
                self.nav.setCurrentRow(i)
                break
        page = self.pages.get(key)
        if focus and hasattr(page, "focus"):
            page.focus(focus)

    def guard_result(self, result) -> None:
        """Tell the user what the startup guard just did (shown on the checkup page)."""
        if result is not None and (result.fixed or result.need_admin):
            self.pages["home"].show_guard_notice(result)
            if "startup" in self.pages and self.pages["startup"].loaded:
                self.pages["startup"].load()

    def closeEvent(self, event) -> None:
        self.store.cancel()
        super().closeEvent(event)


def main() -> int:
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenSweep.OpenSweep")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    errors.install()
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    settings = Settings.load()
    theme.apply(app, settings.theme)
    app.setWindowIcon(app_icon())
    set_language(settings.language or detect())
    windows: list[MainWindow] = []

    def open_window(previous: MainWindow | None = None) -> None:
        """Build the main window; called again to switch skins, since colors are baked into widgets."""
        window = MainWindow(settings)
        window.theme_changed.connect(lambda: rebuild(window))
        if previous is not None:
            window.restoreGeometry(previous.saveGeometry())
            window.show_page("settings")
        window.show()
        windows.append(window)

    def rebuild(old: MainWindow) -> None:
        theme.apply(app, settings.theme)
        open_window(old)
        old.store.cancel()
        old.close()
        old.deleteLater()
        windows.remove(old)

    open_window()
    if "--page" in sys.argv:
        idx = sys.argv.index("--page")
        if idx + 1 < len(sys.argv):
            windows[0].show_page(sys.argv[idx + 1])
    if not settings.onboarded and "--page" not in sys.argv:
        from opensweep.gui.onboarding import WelcomeDialog
        if WelcomeDialog(windows[0], settings).exec():
            windows[0].show_page("home")
            windows[0].pages["home"].quick_start()
    if settings.check_updates:
        windows[0].pages["settings"].check_updates(quiet=True)
    if IS_WINDOWS:
        run_task(_guard_on_open, settings, on_done=lambda r: windows and windows[-1].guard_result(r),
                 on_error=lambda m: errors.log_error(f"guard: {m}"))
    return app.exec()


def _guard_on_open(settings: Settings):
    """Each time OpenSweep opens: switch back off what sneaked back into startup."""
    from opensweep.core import guard, startup
    entries = startup.describe(startup.list_startup())
    guard.seed(entries)
    result = guard.enforce(is_admin(), entries) if settings.guard else None
    guard.sync_logon_check(settings.guard)
    return result


if __name__ == "__main__":
    sys.exit(main())
