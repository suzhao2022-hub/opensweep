"""One-click flow, undo, first-run welcome and the disk page's "start here" list."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from opensweep import i18n
from opensweep.core import checkup, diskmap, startup
from opensweep.gui.onboarding import WelcomeDialog
from opensweep.gui.pages import home as home_mod
from opensweep.gui.store import ScanStore
from opensweep.settings import Settings

MB = 1024 ** 2
NEW_KEYS = ["home.quick", "home.undo", "home.undo_text", "home.undo_done", "done.equiv", "done.open_bin",
            "disk.wins_title", "startup.legend", "welcome.title", "welcome.intro", "welcome.step1",
            "welcome.step2", "welcome.step3", "welcome.guard", "welcome.guard_hint", "welcome.later",
            "welcome.start"]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("language", list(i18n.available()))
def test_new_texts_exist_in_every_language(language):
    i18n.set_language(language)
    for key in NEW_KEYS:
        assert i18n.t(key, count=2, photos=5) != key, (language, key)
    assert "{count}" not in i18n.t("home.undo_text", count=3)
    i18n.set_language("en")


def _entry(name):
    return startup.StartupEntry(name, name + ".exe", "Registry", "HKCU", "Run", True, advice="disable")


def _home(monkeypatch, app):
    store = ScanStore([])
    monkeypatch.setattr(checkup, "collect_facts", lambda: checkup.SystemFacts([], [], [], [], 10.0))
    page = home_mod.HomePage(store, Settings(guard=False))
    return page


def test_quick_button_checks_then_offers_the_fix_once(app, monkeypatch):
    page = _home(monkeypatch, app)
    offered = []
    monkeypatch.setattr(page, "_fix_all_plan", lambda: ["something to do"])
    monkeypatch.setattr(page, "_fix_all", lambda: offered.append(1))
    page.quick_start()
    assert not page.quick_btn.isVisibleTo(page)
    for _ in range(300):
        QCoreApplication.processEvents()
        if offered:
            break
        import time
        time.sleep(0.02)
    assert offered == [1]
    page._render()  # a later refresh must not ask again
    assert offered == [1]
    page.close()


def test_nothing_to_fix_means_no_question(app, monkeypatch):
    page = _home(monkeypatch, app)
    monkeypatch.setattr(page, "_fix_all_plan", lambda: [])
    asked = []
    monkeypatch.setattr(page, "_fix_all", lambda: asked.append(1))
    page.quick_start()
    import time
    for _ in range(150):
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert asked == []
    page.close()


def test_undo_turns_back_on_what_the_fix_turned_off(app, monkeypatch):
    page = _home(monkeypatch, app)
    calls = []

    def fake_apply(enable=(), disable=(), remove_entries=()):
        calls.append((list(enable), list(disable)))
        return startup.ChangeResult(list(enable), [])

    monkeypatch.setattr(startup, "apply_changes", fake_apply)
    monkeypatch.setattr(home_mod, "run_task", lambda fn, *a, on_done=None, **k: on_done(fn(*a)))
    monkeypatch.setattr(page, "_collect_facts", lambda: None)
    a, b = _entry("a"), _entry("b")
    page._undo = [a, b]
    page._undo_startup()
    assert calls == [([a, b], [])] and page._undo == []
    page.close()


def test_welcome_remembers_choices(app):
    settings = Settings(guard=True)
    saved = []
    settings.save = lambda: saved.append((settings.guard, settings.onboarded))
    dialog = WelcomeDialog(None, settings)
    dialog.guard.setChecked(False)
    dialog.accept()
    assert saved == [(False, True)] and settings.onboarded and not settings.guard


def test_start_here_list_only_names_exact_cleanable_folders():
    root = diskmap.Node("C:", "C:\\")
    temp_path = os.environ.get("TEMP") or os.path.expandvars(r"%LOCALAPPDATA%\Temp")
    temp = diskmap.Node("Temp", temp_path, size=900 * MB, parent=root)
    inside = diskmap.Node("junk", os.path.join(temp_path, "junk"), size=900 * MB, parent=temp)
    temp.children.append(inside)
    small = diskmap.Node("Tiny", os.path.join(os.path.dirname(temp_path), "Tiny"), size=1 * MB, parent=root)
    root.children = [temp, small]
    root.size = 901 * MB
    wins = diskmap.quick_wins(root)
    assert [(n.path, k.id) for n, k in wins] == [(temp_path, "temp")]  # not the folder inside it, not tiny ones
    assert diskmap.quick_wins(diskmap.Node("D:", "D:\\")) == []


def test_deleting_from_the_disk_map_updates_the_totals():
    root = diskmap.Node("D:", "D:\\")
    folder = diskmap.Node("movies", "D:\\movies", parent=root)
    film = diskmap.Node("a.mp4", "D:\\movies\\a.mp4", size=700, is_dir=False, parent=folder)
    rest = diskmap.Node("", "D:\\movies", size=50, is_dir=False, kind="small", parent=folder)
    rest.files = 5
    folder.children = [film, rest]
    folder.size, folder.files = 750, 6
    other = diskmap.Node("b.mp4", "D:\\b.mp4", size=100, is_dir=False, parent=root)
    root.children = [folder, other]
    root.size = 850
    m = diskmap.DiskMap(root, [film, other], {"video": [800, 2], "other": [60, 7]}, 7, 1, 0, 0.1)
    diskmap.discount(m, folder)
    diskmap.remove_node(folder)
    assert m.types == {"video": [100, 1], "other": [10, 2]}
    assert m.largest == [other] and root.size == 100 and m.files == 1


def test_other_pages_re_read_startup_entries_after_a_change(app, monkeypatch):
    from opensweep.gui.app import MainWindow
    window = MainWindow(Settings(guard=False))
    pages = window.pages
    pages["startup"].loaded = True
    pages["memory"].startup_by_exe = {"x": object()}
    pages["home"]._stale = False
    pages["memory"].startup_changed.emit()
    assert pages["startup"].loaded is False and pages["home"]._stale
    pages["startup"].loaded = True
    pages["home"].startup_changed.emit()
    assert pages["startup"].loaded is False and pages["memory"].startup_by_exe == {}
    pages["home"]._stale = False
    pages["software"].changed.emit()
    assert pages["home"]._stale
    pages["home"]._stale = False
    pages["disk"].changed.emit()
    assert pages["home"]._stale
    window.close()
