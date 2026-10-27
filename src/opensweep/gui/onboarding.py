"""First-run welcome: what OpenSweep does in three lines, one switch, one button."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QVBoxLayout

from opensweep.gui import theme
from opensweep.gui.widgets import button, icon_label, label
from opensweep.i18n import t
from opensweep.settings import Settings

STEPS = (("scan", "welcome.step1"), ("check", "welcome.step2"), ("history", "welcome.step3"))


class WelcomeDialog(QDialog):
    """accept() = start the checkup now; reject() = look around first."""

    def __init__(self, parent, settings: Settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(t("welcome.title"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(12)
        lay.addWidget(label(t("welcome.title"), "title"))
        lay.addWidget(label(t("welcome.intro"), "muted", wrap=True))
        lay.addSpacing(4)
        for icon, key in STEPS:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(icon_label(icon, theme.C["accent"], 22), 0, Qt.AlignTop)
            row.addWidget(label(t(key), wrap=True), 1)
            lay.addLayout(row)
        lay.addSpacing(4)
        self.guard = QCheckBox(t("welcome.guard"))
        self.guard.setChecked(settings.guard)
        lay.addWidget(self.guard)
        lay.addWidget(label(t("welcome.guard_hint"), "faint", wrap=True))
        lay.addSpacing(6)
        later = button(t("welcome.later"))
        later.clicked.connect(self.reject)
        start = button(t("welcome.start"), "primary", "check")
        start.clicked.connect(self.accept)
        start.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(later)
        row.addWidget(start)
        lay.addLayout(row)

    def done(self, result: int) -> None:
        self.settings.guard = self.guard.isChecked()
        self.settings.onboarded = True
        self.settings.save()
        super().done(result)
