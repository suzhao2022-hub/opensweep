"""Settings and About."""

from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from opensweep import REPO_URL, __version__
from opensweep.core import feedback
from opensweep.core.platform import log_dir, open_in_file_manager, user_rules_dir
from opensweep.gui import theme
from opensweep.gui.widgets import Card, PageHeader, button, confirm, hbox, info, label, page_layout
from opensweep.gui.workers import run_task
from opensweep.i18n import available, t
from opensweep.settings import Settings


class ThemeCard(QFrame):
    """A clickable mini preview of one skin."""
    clicked = Signal(str)

    def __init__(self, name: str, selected: bool):
        super().__init__()
        self.name = name
        self.setObjectName("themeCard")
        self.setProperty("selected", "true" if selected else "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(210)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        side, page, accent, card = theme.PREVIEWS[name]
        preview = QFrame()
        preview.setFixedHeight(92)
        preview.setStyleSheet(f"background: {page}; border-radius: 6px;")
        pl = QHBoxLayout(preview)
        pl.setContentsMargins(0, 0, 8, 0)
        pl.setSpacing(8)
        sidebar = QFrame()
        sidebar.setFixedWidth(46)
        sidebar.setStyleSheet(f"background: {side}; border-radius: 6px;")
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(6, 10, 6, 10)
        for i in range(4):
            dot = QFrame()
            dot.setFixedHeight(8)
            dot.setStyleSheet(f"background: {accent if i == 0 else '#669AA4B4'}; border-radius: 4px;")
            sl.addWidget(dot)
        sl.addStretch(1)
        pl.addWidget(sidebar)
        body = QVBoxLayout()
        body.setContentsMargins(0, 10, 0, 10)
        hero = QFrame()
        hero.setFixedHeight(34)
        hero.setStyleSheet(f"background: {accent if name == 'fresh' else card}; border-radius: 6px;"
                           f"{'' if name == 'fresh' else 'border: 1px solid ' + accent + ';'}")
        body.addWidget(hero)
        tile = QFrame()
        tile.setFixedHeight(22)
        tile.setStyleSheet(f"background: {card}; border-radius: 5px;")
        body.addWidget(tile)
        pl.addLayout(body, 1)
        lay.addWidget(preview)
        lay.addWidget(label(t(f"theme.{name}"), "section"))
        lay.addWidget(label(t(f"theme.{name}_d"), "faint", wrap=True))

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.name)


class SettingsPage(QWidget):
    theme_changed = Signal()

    def __init__(self, settings: Settings, rule_errors: list[str]):
        super().__init__()
        self.settings = settings
        lay = page_layout(self)
        lay.addWidget(PageHeader(t("settings.title")))

        look = Card()
        look.layout_.addWidget(label(t("settings.theme"), "section"))
        look.layout_.addWidget(label(t("settings.theme_hint"), "muted", wrap=True))
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for name in theme.THEMES:
            card = ThemeCard(name, name == theme.NAME)
            card.clicked.connect(self._on_theme)
            cards.addWidget(card)
        cards.addStretch(1)
        look.layout_.addLayout(cards)
        lay.addWidget(look)

        general = Card()
        general.layout_.addWidget(label(t("settings.language"), "section"))
        self.lang = QComboBox()
        self.lang.addItem(t("settings.language_auto"), "")
        for code, name in available().items():
            self.lang.addItem(name, code)
        idx = self.lang.findData(settings.language)
        self.lang.setCurrentIndex(max(idx, 0))
        self.lang.currentIndexChanged.connect(self._on_lang)
        general.layout_.addLayout(hbox(self.lang, stretch_at=1))
        general.layout_.addSpacing(8)
        self.recycle = QCheckBox(t("settings.recycle"))
        self.recycle.setChecked(settings.use_recycle_bin)
        self.recycle.toggled.connect(self._on_recycle)
        general.layout_.addWidget(self.recycle)
        general.layout_.addWidget(label(t("settings.recycle_hint"), "muted", wrap=True))
        lay.addWidget(general)

        rules = Card()
        rules.layout_.addWidget(label(t("settings.rules"), "section"))
        rules.layout_.addWidget(label(t("settings.rules_hint"), "muted", wrap=True))
        if rule_errors:
            rules.layout_.addWidget(label(t("settings.rule_errors", count=len(rule_errors)) + "\n"
                                          + "\n".join(rule_errors[:8]), wrap=True))
        open_rules = button(t("settings.open_rules"))
        open_rules.clicked.connect(lambda: open_in_file_manager(user_rules_dir()))
        open_logs = button(t("settings.open_logs"))
        open_logs.clicked.connect(lambda: open_in_file_manager(log_dir()))
        rules.layout_.addLayout(hbox(open_rules, open_logs, stretch_at=2))
        lay.addWidget(rules)

        about = Card()
        about.layout_.addWidget(label(t("settings.about"), "section"))
        about.layout_.addWidget(label(t("settings.about_text", version=__version__), wrap=True))
        self.auto_update = QCheckBox(t("settings.check_updates_auto"))
        self.auto_update.setChecked(settings.check_updates)
        self.auto_update.toggled.connect(self._on_auto_update)
        about.layout_.addWidget(self.auto_update)
        self.update_btn = button(t("settings.check_updates"), None, "refresh")
        self.update_btn.clicked.connect(self.check_updates)
        report = button(t("feedback.report_bug"), None, "info")
        report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(
            feedback.issue_url("bug", "", {"Page": "Settings"}))))
        site = button(t("settings.website"))
        site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        about.layout_.addLayout(hbox(self.update_btn, report, site, stretch_at=3))
        lay.addWidget(about)
        lay.addStretch(1)

    # ------------------------------------------------------------- updates

    def check_updates(self, quiet: bool = False) -> None:
        """Look for a newer release. `quiet`: only speak up if there is one (used at start-up)."""
        self.update_btn.setEnabled(False)
        self.update_btn.setText(t("settings.checking"))

        def done(release) -> None:
            self.update_btn.setEnabled(True)
            self.update_btn.setText(t("settings.check_updates"))
            if release is None:
                if not quiet:
                    info(self, t("settings.check_updates"), t("settings.update_failed"))
            elif feedback.is_newer(release.version):
                if confirm(self, t("settings.check_updates"),
                           t("settings.update_available", version=release.version, current=__version__),
                           t("settings.update_download")):
                    QDesktopServices.openUrl(QUrl(release.url))
            elif not quiet:
                info(self, t("settings.check_updates"), t("settings.up_to_date", version=__version__))

        run_task(feedback.latest_release, on_done=done, on_error=lambda _m: done(None))

    def _on_theme(self, name: str) -> None:
        if name == self.settings.theme and name == theme.NAME:
            return
        self.settings.theme = name
        self.settings.save()
        self.theme_changed.emit()

    def _on_auto_update(self, on: bool) -> None:
        self.settings.check_updates = on
        self.settings.save()

    def _on_lang(self) -> None:
        self.settings.language = self.lang.currentData()
        self.settings.save()
        info(self, t("settings.language"), t("settings.restart_needed"))

    def _on_recycle(self, on: bool) -> None:
        self.settings.use_recycle_bin = on
        self.settings.save()
