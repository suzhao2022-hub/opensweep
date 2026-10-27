import pytest

from opensweep.core import guard, startup


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "config_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(startup, "set_enabled", lambda e, on: (calls.append((e.name, on)), setattr(e, "enabled", on)))
    return calls


def _entry(name, enabled=True, hive="HKCU", kind="Run"):
    return startup.StartupEntry(name, f"C:\\x\\{name}.exe", "", hive, kind, enabled, reg_path=startup.RUN)


def test_remembered_entry_that_comes_back_is_turned_off_again(isolated):
    douyin = _entry("douyin", enabled=False)
    guard.remember([douyin])
    douyin.enabled = True  # the app turned itself back on
    result = guard.enforce(admin=False, entries=[douyin, _entry("qq")])
    assert [e.name for e in result.fixed] == ["douyin"]
    assert isolated == [("douyin", False)]
    assert guard.history()[0]["display"] == "douyin"


def test_entries_needing_admin_are_reported_not_forced(isolated):
    svc = _entry("updater", enabled=False, hive="SYS", kind="Task")
    guard.remember([svc])
    svc.enabled = True
    result = guard.enforce(admin=False, entries=[svc])
    assert result.fixed == [] and [e.name for e in result.need_admin] == ["updater"]
    assert isolated == []


def test_forget_and_seed():
    a, b = _entry("a", enabled=False), _entry("b", enabled=True)
    guard.seed([a, b])
    assert guard.watched_count() == 1
    guard.seed([_entry("c", enabled=False)])  # seeding happens only once
    assert guard.watched_count() == 1
    guard.forget([a])
    assert guard.watched_count() == 0


def test_own_guard_entry_is_never_switched_off():
    own = _entry(startup.GUARD_VALUE, enabled=True)
    guard.remember([own])
    assert guard.came_back([own]) == []
