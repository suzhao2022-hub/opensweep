"""Launch sources beyond Run keys: RunOnce, policy Run, Store app tasks, Windows' reopen-apps setting."""
from types import SimpleNamespace

import pytest

from opensweep.core import startup, startup_store as store


class Key:
    def __init__(self, reg, hive, path):
        self.reg, self.id = reg, (hive, path.lower())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Registry:
    """Just enough of winreg: case-insensitive paths and names that keep the case they were written with."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_SET_VALUE, REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD = 2, 1, 2, 3, 4

    def __init__(self):
        self.data = {}   # (hive, lowercase path) -> {name: (value, type)}
        self.paths = {}  # (hive, lowercase path) -> path as written

    def _split(self, hive, path):
        if isinstance(hive, Key):
            hive, path = hive.id[0], self.paths[hive.id] + "\\" + path
        return hive, path

    def OpenKey(self, hive, path, *args):
        hive, path = self._split(hive, path)
        if (hive, path.lower()) not in self.data:
            raise FileNotFoundError(path)
        return Key(self, hive, path.lower())

    def CreateKeyEx(self, hive, path, *args):
        hive, path = self._split(hive, path)
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            part = "\\".join(parts[:i])
            self.data.setdefault((hive, part.lower()), {})
            self.paths.setdefault((hive, part.lower()), part)
        return Key(self, hive, path.lower())

    def _find(self, key, name):
        return next((n for n in self.data[key.id] if n.lower() == name.lower()), None)

    def QueryValueEx(self, key, name):
        found = self._find(key, name)
        if found is None:
            raise FileNotFoundError(name)
        return self.data[key.id][found]

    def SetValueEx(self, key, name, reserved, typ, value):
        self.data[key.id].pop(self._find(key, name), None)
        self.data[key.id][name] = (value, typ)

    def DeleteValue(self, key, name):
        found = self._find(key, name)
        if found is None:
            raise FileNotFoundError(name)
        del self.data[key.id][found]

    def _children(self, key):
        prefix = self.paths[key.id] + "\\"
        return sorted({p[len(prefix):].split("\\")[0] for (h, _), p in self.paths.items()
                       if h == key.id[0] and p.lower().startswith(prefix.lower())})

    def QueryInfoKey(self, key):
        return len(self._children(key)), len(self.data[key.id]), 0

    def EnumKey(self, key, index):
        return self._children(key)[index]

    def EnumValue(self, key, index):
        name, (value, typ) = list(self.data[key.id].items())[index]
        return name, value, typ

    def put(self, hive, path, name, value, typ=1):
        with self.CreateKeyEx(hive, path) as key:
            self.SetValueEx(key, name, 0, typ, value)


@pytest.fixture
def registry(monkeypatch):
    reg = Registry()
    monkeypatch.setattr(startup, "winreg", reg, raising=False)
    monkeypatch.setattr(store, "winreg", reg, raising=False)
    monkeypatch.setattr(startup, "IS_WINDOWS", True)
    monkeypatch.setattr(startup, "sys", SimpleNamespace(getwindowsversion=lambda: SimpleNamespace(build=22631)))
    return reg


def test_runonce_and_policy_run_are_listed_and_reversible(registry):
    registry.put("HKCU", startup.RUNONCE, "Installer", r"C:\x\setup.exe /resume")
    registry.put("HKLM", startup.POLICY_RUN, "Agent", r"C:\x\agent.exe")
    entries = {e.name: e for e in startup._run_entries("HKCU", startup.RUNONCE, "RunOnce", "once")
               + startup._run_entries("HKLM", startup.POLICY_RUN, "PolicyRun", "policy")}
    assert entries["Installer"].enabled and entries["Agent"].enabled

    startup.set_enabled(entries["Installer"], False)
    live = startup._run_entries("HKCU", startup.RUNONCE, "RunOnce", "once")
    assert [(e.name, e.enabled) for e in live] == [("Installer", False)]  # still shown, from the backup
    assert ("HKCU", startup.RUNONCE.lower()) in registry.data
    assert "Installer" not in registry.data[("HKCU", startup.RUNONCE.lower())]

    startup.set_enabled(live[0], True)
    assert registry.data[("HKCU", startup.RUNONCE.lower())]["Installer"] == (r"C:\x\setup.exe /resume", 1)
    # No StartupApproved key is created for sources that do not use one.
    assert not any("startupapproved\\runonce" in p for _, p in registry.data)


def test_store_app_task_toggles_and_respects_policy(registry):
    base = startup.STORE_TASKS + r"\Spotify_abc"
    registry.put("HKCU", base + r"\SpotifyStartupId", "State", 2, 4)
    registry.put("HKCU", base + r"\PolicyTask", "State", 4, 4)
    entries = {e.extra["task"]: e for e in startup._store_entries()}
    spotify = entries["SpotifyStartupId"]
    assert spotify.name == "Spotify" and spotify.enabled and not spotify.needs_admin and not spotify.removable

    startup.set_enabled(spotify, False)
    assert startup._store_state(spotify.reg_path) == 1
    startup.set_enabled(spotify, True)
    assert startup._store_state(spotify.reg_path) == 2

    with pytest.raises(OSError, match="policy"):
        startup.set_enabled(entries["PolicyTask"], False)
    assert startup._store_state(entries["PolicyTask"].reg_path) == 4


def test_restart_apps_setting(registry, monkeypatch):
    registry.put("HKCU", startup.WINLOGON_USER, startup.RESTART_APPS, 1, 4)
    [entry] = startup._restart_entries()
    assert entry.enabled and entry.approved_key == "RestartApps" and not entry.removable
    startup.set_enabled(entry, False)
    assert startup._restart_apps_value() == 0
    assert not startup._restart_entries()[0].enabled
    startup.set_enabled(entry, True)
    assert startup._restart_apps_value() == 1


def test_restart_apps_default_depends_on_windows_build(registry, monkeypatch):
    [entry] = startup._restart_entries()  # never written, Windows 11 22H2+
    assert entry.enabled and entry.extra["default"]
    startup.set_enabled(entry, False)
    assert startup._restart_apps_value() == 0 and not entry.extra["default"]

    registry.data.clear()
    monkeypatch.setattr(startup, "sys", SimpleNamespace(getwindowsversion=lambda: SimpleNamespace(build=19045)))
    assert startup._restart_entries() == []  # older Windows: only shown once the value exists


def test_launch_points_report_is_read_only_and_flags_odd_winlogon(registry):
    winlogon = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
    registry.put("HKLM", winlogon, "Userinit", r"C:\Windows\system32\userinit.exe,C:\evil\x.exe,")
    registry.put("HKLM", winlogon, "Shell", "explorer.exe")
    registry.put("HKLM", r"Software\Microsoft\Active Setup\Installed Components\{G1}", "StubPath",
                 r"C:\Program Files\App\setup.exe /user")
    registry.put("HKLM", r"Software\Microsoft\Active Setup\Installed Components\{G2}", "StubPath",
                 r"C:\Windows\system32\regsvr32.exe /s x.dll")
    before = {k: dict(v) for k, v in registry.data.items()}
    points = {(p["name"]): p for p in startup.launch_points()}
    assert points["Userinit"]["unusual"] and not points["Shell"]["unusual"]
    assert "{G1}" in points and "{G2}" not in points
    assert points["RestartApps"]["value"] == "not set"
    assert registry.data == before


def test_describe_skips_program_lookup_for_settings(registry):
    registry.put("HKCU", startup.WINLOGON_USER, startup.RESTART_APPS, 1, 4)
    [entry] = startup.describe(startup._restart_entries())
    assert entry.exe == "" and entry.exists
