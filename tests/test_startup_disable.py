"""Regression tests for preserving originals while removing launch sources."""
from types import SimpleNamespace

import pytest

from opensweep.core import guard, startup, startup_store as store


class Key(tuple):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Registry:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_SET_VALUE, REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD = 2, 1, 2, 3, 4

    def __init__(self):
        self.data = {}
        self.fail_backup = False
        self.fail_delete = False

    def OpenKey(self, hive, path, *args):
        key = (hive, path.lower())
        if key not in self.data:
            raise FileNotFoundError(path)
        return Key(key)

    def CreateKeyEx(self, hive, path, *args):
        key = (hive, path.lower())
        if self.fail_backup and path.startswith(store.SAVED):
            raise PermissionError(path)
        self.data.setdefault(key, {})
        return Key(key)

    def QueryValueEx(self, key, name):
        try:
            return self.data[key][name.lower()]
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, key, name, reserved, typ, value):
        self.data[key][name.lower()] = (value, typ)

    def DeleteValue(self, key, name):
        if self.fail_delete:
            raise PermissionError(name)
        if name.lower() not in self.data[key]:
            raise FileNotFoundError(name)
        del self.data[key][name.lower()]

    def QueryInfoKey(self, key):
        return 0, len(self.data[key]), 0

    def EnumValue(self, key, index):
        name, (value, typ) = list(self.data[key].items())[index]
        return name, value, typ

    def put(self, hive, path, name, value, typ=1):
        with self.CreateKeyEx(hive, path) as key:
            self.SetValueEx(key, name, 0, typ, value)


@pytest.fixture
def registry(monkeypatch, tmp_path):
    reg = Registry()
    monkeypatch.setattr(startup, "winreg", reg, raising=False)
    monkeypatch.setattr(store, "winreg", reg, raising=False)
    monkeypatch.setattr(guard, "config_dir", lambda: tmp_path)
    return reg


def entry(name="App"):
    return startup.StartupEntry(name, r'"%LOCALAPPDATA%\App\app.exe" --tray',
                                "Registry", "HKCU", "Run", True, reg_path=startup.RUN)


def test_run_is_removed_but_exact_expandable_command_can_be_restored(registry):
    e = entry()
    registry.put("HKCU", startup.RUN, e.name, e.command, registry.REG_EXPAND_SZ)
    startup.set_enabled(e, False)
    assert not e.enabled and store._live_value(e) is None
    listed = startup._run_entries("HKCU", startup.RUN, "Run", "Registry")
    assert len(listed) == 1 and not listed[0].enabled and listed[0].command == e.command
    startup.set_enabled(listed[0], True)
    assert store._live_value(e) == (e.command, registry.REG_EXPAND_SZ)
    assert store.values("HKCU", "Run") == {}


def test_saved_entry_is_visible_even_if_live_run_key_is_removed(registry):
    e = entry()
    registry.put("HKCU", startup.RUN, e.name, e.command)
    startup.set_enabled(e, False)
    del registry.data[("HKCU", startup.RUN.lower())]
    assert startup._run_entries("HKCU", startup.RUN, "Run", "Registry")[0].enabled is False


def test_backup_failure_never_removes_original(registry):
    e = entry()
    registry.put("HKCU", startup.RUN, e.name, e.command)
    registry.fail_backup = True
    with pytest.raises(PermissionError):
        startup.set_enabled(e, False)
    assert e.enabled and store._live_value(e)[0] == e.command


def test_delete_failure_is_reported_and_backup_survives(registry):
    e = entry()
    registry.put("HKCU", startup.RUN, e.name, e.command)
    registry.fail_delete = True
    with pytest.raises(PermissionError):
        startup.set_enabled(e, False)
    assert e.enabled and store._live_value(e) is not None
    assert store.values("HKCU", "Run")


def test_recreated_run_command_is_detected_even_if_approval_stays_off(registry):
    e = entry()
    registry.put("HKCU", startup.RUN, e.name, e.command)
    startup.set_enabled(e, False)
    guard.remember([e])
    new_command = r'"C:\App\v2\app.exe" --new'
    registry.put("HKCU", startup.RUN, e.name, new_command)
    live = startup._run_entries("HKCU", startup.RUN, "Run", "Registry")
    assert len(live) == 1 and live[0].extra["needs_block"]
    assert guard.enforce(False, live).fixed == live
    assert store._live_value(e) is None
    startup.set_enabled(live[0], True)
    assert store._live_value(e)[0] == new_command


def test_legacy_flag_only_disable_is_upgraded_without_losing_restore(registry):
    e = entry()
    registry.put("HKCU", startup.RUN, e.name, e.command)
    startup._write_approved(e, False)
    entries = startup._run_entries("HKCU", startup.RUN, "Run", "Registry")
    guard.seed(entries)
    assert guard.enforce(False, entries).fixed
    assert store._live_value(e) is None
    startup.set_enabled(entries[0], True)
    assert store._live_value(e)[0] == e.command


def test_missing_entry_cannot_be_restored_from_stale_ui_data(registry):
    with pytest.raises(FileNotFoundError):
        startup.set_enabled(entry(), True)


def test_folder_entry_is_moved_out_of_startup_and_restored(registry, tmp_path):
    folder = tmp_path / "Startup"
    folder.mkdir()
    original = folder / "app.cmd"
    original.write_text("echo example")
    e = startup.StartupEntry(original.name, str(original), "Folder", "HKCU",
                             "StartupFolder", True, reg_path=str(original))
    startup.set_enabled(e, False)
    assert not original.exists() and store.folder_backup(original).is_file()
    listed = startup._folder_entries(str(folder), "HKCU", "Folder")
    assert len(listed) == 1 and not listed[0].enabled
    startup.set_enabled(listed[0], True)
    assert original.read_text() == "echo example"
    assert not store.folder_backup(original).exists()


def test_folder_restore_never_overwrites_new_shortcut(registry, tmp_path):
    original = tmp_path / "Startup" / "app.lnk"
    original.parent.mkdir()
    original.write_bytes(b"old")
    e = startup.StartupEntry(original.name, str(original), "", "HKCU", "StartupFolder",
                             True, reg_path=str(original))
    startup.set_enabled(e, False)
    original.write_bytes(b"new")
    with pytest.raises(FileExistsError):
        startup.set_enabled(e, True)
    assert original.read_bytes() == b"new"
    assert store.folder_backup(original).read_bytes() == b"old"


@pytest.mark.parametrize("original_mode,delayed", [(2, 1), (2, 0), (3, 0)])
def test_service_is_disabled_and_original_mode_is_restored(registry, monkeypatch, original_mode, delayed):
    path = r"SYSTEM\CurrentControlSet\Services\VendorSvc"
    registry.put("HKLM", path, "Start", original_mode, registry.REG_DWORD)
    registry.put("HKLM", path, "DelayedAutoStart", delayed, registry.REG_DWORD)
    e = startup.StartupEntry("Vendor Service", "svc.exe", "", "SYS", "Service",
                             True, reg_path="VendorSvc")

    def powershell(script, **kw):
        mode = 4 if " Disabled " in script else 3 if " Manual " in script else 2
        registry.put("HKLM", path, "Start", mode, registry.REG_DWORD)
        registry.put("HKLM", path, "DelayedAutoStart", 0, registry.REG_DWORD)
        return ""
    monkeypatch.setattr(startup, "_powershell", powershell)
    startup.set_enabled(e, False)
    assert not e.enabled and store.service_state("VendorSvc")["start"] == 4
    startup.set_enabled(e, False)  # guard retries must preserve the first original
    startup.set_enabled(e, True)
    assert store.service_state("VendorSvc") == {"start": original_mode, "delayed": delayed}
    assert store.values("HKLM", "Service") == {}


def test_service_readback_failure_does_not_report_success(registry, monkeypatch):
    path = r"SYSTEM\CurrentControlSet\Services\VendorSvc"
    registry.put("HKLM", path, "Start", 2, registry.REG_DWORD)
    e = startup.StartupEntry("Svc", "svc.exe", "", "SYS", "Service", True, reg_path="VendorSvc")
    monkeypatch.setattr(startup, "_powershell", lambda *a, **k: "")
    with pytest.raises(OSError, match="did not change"):
        startup.set_enabled(e, False)
    assert e.enabled and store.values("HKLM", "Service")


def test_nonzero_powershell_exit_is_never_success_even_without_stderr(monkeypatch):
    monkeypatch.setattr(startup.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(OSError, match="code 1"):
        startup._powershell("unused")


def test_disabled_services_remain_visible_and_manual_watched_services_are_migrated(monkeypatch):
    from opensweep.core import software
    monkeypatch.setattr(software, "exe_company", lambda p: "Vendor")
    rows = [{"kind": "service", "name": n, "start_mode": m,
             "command": r"C:\Vendor\svc.exe"} for n, m in
            [("Off", "Disabled"), ("Watched", "Manual"), ("Unrelated", "Manual")]]
    found = startup.parse_system_entries(rows, {"watched"})
    assert [(e.name, e.enabled) for e in found] == [("Off", False), ("Watched", True)]
