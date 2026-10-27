import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from opensweep import i18n
from opensweep.core.boottrace import BootReport
from opensweep.gui import boot_dialog
from opensweep.gui.pages.startup import StartupPage
from opensweep.settings import Settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("language", list(i18n.available()))
def test_startup_page_and_diagnostic_open_in_each_language(app, monkeypatch, language):
    i18n.set_language(language)
    page = StartupPage(Settings(guard=False))
    assert any(b.text() == i18n.t("boot.button") for b in page.findChildren(QPushButton))
    empty = BootReport(150, 100, [], [], [], {})
    monkeypatch.setattr(boot_dialog, "run_task", lambda fn, on_done, **kw: on_done(empty))
    dialog = boot_dialog.BootDialog(page)
    assert dialog.copy_btn.isEnabled()  # empty process list still has valuable settings
    assert "boot." not in dialog.intro.text()
    assert '"version": "1.0.1"' in boot_dialog.report_text(dialog.report)
    dialog.close()
    page.close()
    i18n.set_language("en")


def _entry(name, enabled=True, advice="disable", exists=True):
    from opensweep.core.startup import StartupEntry
    return StartupEntry(name, name + ".exe", "Registry", "HKCU", "Run", enabled, advice=advice, exists=exists)


def test_bulk_buttons_act_on_selection_and_everything_unneeded(app, monkeypatch):
    from opensweep.gui.pages import startup as page_mod
    page = StartupPage(Settings(guard=False))
    page._fill([_entry("a"), _entry("b"), _entry("off", enabled=False), _entry("win", advice="keep"),
                _entry("gone", exists=False), _entry(page_mod.startup.GUARD_VALUE, advice="optional")])
    calls = []
    monkeypatch.setattr(page, "_apply", lambda enable, disable, remove, quiet=False:
                        calls.append(([e.name for e in enable], [e.name for e in disable], remove)))
    assert not page.off_sel_btn.isEnabled() and page.off_all_btn.isEnabled()

    names = [page._entry(r).name for r in range(page.table.rowCount())]
    model, flags = page.table.selectionModel(), QItemSelectionModel.Select | QItemSelectionModel.Rows
    for name in ("a", "b", "off"):
        model.select(page.table.model().index(names.index(name), 0), flags)
    assert page.off_sel_btn.isEnabled() and page.on_sel_btn.isEnabled()
    page.off_sel_btn.click()
    page.on_sel_btn.click()
    assert sorted(calls[0][1]) == ["a", "b"] and calls[1] == (["off"], [], [])

    monkeypatch.setattr(page_mod, "confirm", lambda *a, **k: True)
    page.off_all_btn.click()
    assert sorted(calls[2][1]) == ["a", "b"]  # not the kept, broken, already-off or guard entries
    page.close()


def test_status_column_follows_the_real_state(app):
    page = StartupPage(Settings(guard=False))
    entry = _entry("a")
    page._fill([entry])
    assert page.table.item(0, 1).text() == i18n.t("startup.on")
    entry.enabled = False  # what apply_changes does once the entry is really off
    page._sync_checks()
    assert page.table.item(0, 1).text() == i18n.t("startup.off")
    assert page.table.item(0, 0).checkState() == Qt.Unchecked
    page.close()


def test_startup_list_appears_before_tasks_and_services_are_read(app, monkeypatch):
    from opensweep.gui.pages import startup as page_mod
    queued, calls = [], []
    fast, full = [_entry("run-entry")], [_entry("run-entry"), _entry("task-entry")]

    def fake_load(include_system=True, apps=None):
        calls.append((include_system, apps))
        return (full if include_system else fast), ["apps"]

    monkeypatch.setattr(page_mod, "load_entries", fake_load)
    monkeypatch.setattr(page_mod, "run_task",
                        lambda fn, *a, on_done=None, on_error=None, **k: queued.append((fn, a, on_done)))
    page = StartupPage(Settings(guard=False))
    page.load()
    fn, args, done = queued.pop()
    done(fn(*args))                                   # first answer: registry and Startup folder only
    assert page.table.rowCount() == 1 and page._busy and not page.table.isEnabled()
    page._apply([], [fast[0]], [])                    # changes wait until the full list is in
    assert len(queued) == 1
    fn, args, done = queued.pop()
    done(fn(*args))                                   # second answer: tasks and services are added
    assert calls == [(False, None), (True, ["apps"])]
    assert page.table.rowCount() == 2 and not page._busy and page.table.isEnabled()
    page.close()


def test_a_newer_load_wins_over_a_slow_older_one(app, monkeypatch):
    from opensweep.gui.pages import startup as page_mod
    queued = []
    monkeypatch.setattr(page_mod, "load_entries", lambda include_system=True, apps=None: ([_entry("x")], []))
    monkeypatch.setattr(page_mod, "run_task",
                        lambda fn, *a, on_done=None, on_error=None, **k: queued.append((fn, a, on_done)))
    page = StartupPage(Settings(guard=False))
    page.load()
    old = queued.pop()
    page.load()
    fn, args, done = old
    done(fn(*args))  # the older load answers late and is ignored
    assert page.table.rowCount() == 0 and len(queued) == 1
    page.close()
