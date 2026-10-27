import struct
from pathlib import Path

from opensweep.core import engine, mft, providers, startup
from opensweep.core.platform import platform_key
from opensweep.core.rules import Rule


def _file(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


# ------------------------------------------------------------------ Chromium app caches

def _profile(root: Path, caches=("Cache", "GPUCache")) -> None:
    _file(root / "Preferences", 2)
    _file(root / "Local Storage" / "leveldb" / "x.ldb", 5)  # must never be touched
    for c in caches:
        _file(root / c / "data_1", 100)


def test_finds_chromium_apps_and_groups_by_app(tmp_path):
    base = tmp_path / "Roaming"
    _profile(base / "douyin")
    _profile(base / "Tencent" / "QQ" / "User Data" / "Default", ("Cache", "Code Cache"))
    _file(base / "PlainApp" / "Cache" / "x", 5)  # no Chromium marker: ignored
    apps = {a.label: a for a in providers.find_chromium_apps([str(base)])}
    assert set(apps) == {"douyin", "QQ"}
    assert len(apps["QQ"].caches) == 2 and apps["QQ"].root.endswith("QQ")


def test_chromium_target_cleaning(tmp_path, monkeypatch):
    base = tmp_path / "Roaming"
    _profile(base / "douyin")
    _profile(base / "Covered")
    original = providers.find_chromium_apps
    monkeypatch.setattr(providers, "find_chromium_apps", lambda: original([str(base)]))
    monkeypatch.setattr(engine, "log_dir", lambda: tmp_path)
    specific = Rule(id="covered", category="apps", name={"en": "x"},
                    paths={platform_key(): [str(base / "Covered" / "Cache")]})
    generic = Rule(id="web", category="apps", name={"en": "web"}, paths={platform_key(): []},
                   provider="chromium", itemize=1, fallback=True)
    results = {r.rule.id: r for r in engine.scan([specific, generic])}
    labels = [t.label for t in results["web"].targets]
    assert labels == ["douyin"]  # "Covered" has its own rule
    target = results["web"].targets[0]
    assert target.size == 200
    engine.clean([target])
    assert not any((base / "douyin" / "Cache").iterdir())
    assert (base / "douyin" / "Local Storage" / "leveldb" / "x.ldb").exists()
    assert (base / "douyin" / "Preferences").exists()


# ------------------------------------------------------------------ scheduled tasks and services

def test_parse_system_entries(monkeypatch):
    import opensweep.core.software as software
    monkeypatch.setattr(software, "exe_company", lambda exe: "Microsoft Corporation" if "ms" in exe else "Vendor")
    items = [
        {"kind": "task", "name": "Updater", "path": "\\", "state": "Ready", "triggers": "MSFT_TaskLogonTrigger",
         "command": r"C:\Vendor\update.exe /silent"},
        {"kind": "task", "name": "Manual", "path": "\\", "state": "Ready", "triggers": "MSFT_TaskEventTrigger",
         "command": r"C:\Vendor\x.exe"},
        {"kind": "task", "name": "Off", "path": "\\V\\", "state": "Disabled", "triggers": "MSFT_TaskBootTrigger",
         "command": r"C:\Vendor\y.exe"},
        {"kind": "service", "name": "VendorSvc", "display": "Vendor Service", "state": "Running",
         "command": r'"C:\Program Files\Vendor\svc.exe" -k'},
        {"kind": "service", "name": "WinSvc", "display": "Win", "state": "Running",
         "command": r"C:\Windows\system32\svchost.exe -k netsvcs"},
        {"kind": "service", "name": "msupdate", "display": "MS thing", "state": "Running",
         "command": r"C:\Program Files\ms\health.exe"},
    ]
    entries = {e.name: e for e in startup.parse_system_entries(items)}
    assert set(entries) == {"Updater", "Off", "Vendor Service"}
    assert entries["Off"].enabled is False and entries["Updater"].enabled is True
    assert entries["Vendor Service"].approved_key == "Service" and not entries["Vendor Service"].removable
    assert entries["Updater"].needs_admin


def test_disabled_task_that_is_still_running_counts_as_off():
    items = [{"kind": "task", "name": "Guard", "path": "\\V\\", "state": "Running", "enabled": False,
              "triggers": "MSFT_TaskLogonTrigger", "command": r"C:\Vendor\guard.exe"},
             {"kind": "task", "name": "Live", "path": "\\V\\", "state": "Running", "enabled": True,
              "triggers": "MSFT_TaskLogonTrigger", "command": r"C:\Vendor\live.exe"}]
    entries = {e.name: e for e in startup.parse_system_entries(items)}
    assert entries["Guard"].enabled is False and entries["Live"].enabled is True


# ------------------------------------------------------------------ MFT helpers

def test_mft_geometry_and_runs():
    boot = bytearray(512)
    boot[3:11] = b"NTFS    "
    struct.pack_into("<H", boot, 0x0B, 512)
    boot[0x0D] = 8
    struct.pack_into("<Q", boot, 0x30, 786432)
    struct.pack_into("<b", boot, 0x40, -10)
    geo = mft._geometry(bytes(boot))
    assert (geo.sector, geo.cluster, geo.record, geo.mft_lcn) == (512, 4096, 1024, 786432)
    # two runs: 0x20 clusters at LCN 0x1000, then 0x10 clusters 0x100 further on
    runs = bytes([0x21, 0x20, 0x00, 0x10, 0x21, 0x10, 0x00, 0x01, 0x00])
    assert mft._data_runs(runs, 0) == [(0x1000, 0x20), (0x1100, 0x10)]


def test_mft_fixup():
    rec = bytearray(1024)
    rec[0:4] = b"FILE"
    struct.pack_into("<HH", rec, 4, 0x30, 3)        # USA at 0x30, 3 entries (check + 2 sectors)
    rec[0x30:0x32] = b"\xAB\xCD"                     # check value
    rec[0x32:0x34] = b"\x11\x22"                     # real bytes of sector 1 end
    rec[0x34:0x36] = b"\x33\x44"                     # real bytes of sector 2 end
    rec[510:512] = b"\xAB\xCD"
    rec[1022:1024] = b"\xAB\xCD"
    assert mft._fixup(rec, 0, 1024, 512)
    assert rec[510:512] == b"\x11\x22" and rec[1022:1024] == b"\x33\x44"
    torn = bytearray(rec)
    torn[0x30:0x32] = b"\x00\x01"
    torn[510:512] = b"\xFF\xFF"
    assert not mft._fixup(torn, 0, 1024, 512)
