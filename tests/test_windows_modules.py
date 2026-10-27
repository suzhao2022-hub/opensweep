import codecs
import os
import struct
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_userassist_entry_parsing():
    from opensweep.core import software
    path = r"{6D809377-6AF0-444B-8957-A3773F02200E}\Foo\foo.exe"
    data = bytearray(72)
    struct.pack_into("<I", data, 4, 7)
    filetime = int((1_700_000_000 + 11644473600) * 10_000_000)
    struct.pack_into("<Q", data, 60, filetime)
    parsed = software.parse_userassist_entry(codecs.encode(path, "rot13"), bytes(data))
    assert parsed is not None
    exe, last, count = parsed
    assert exe.endswith(os.path.normcase(r"\foo\foo.exe")) and "program files" in exe
    assert count == 7 and abs(last - 1_700_000_000) < 1


def test_split_uninstall_command():
    from opensweep.core.software import _split_command
    assert _split_command(r'"C:\Program Files\App\unins000.exe" /SILENT') == \
        [r"C:\Program Files\App\unins000.exe", "/SILENT"]
    assert _split_command(r"C:\Program Files\App\uninstall.exe") == [r"C:\Program Files\App\uninstall.exe"]
    assert _split_command("MsiExec.exe /X{1234}")[0] == "MsiExec.exe"


def test_clean_name():
    from opensweep.core.software import _clean_name
    assert _clean_name("7-Zip 24.08 (x64)") == "7-Zip"
    assert _clean_name("Foo Bar") == "Foo Bar"


def test_leftovers_skip_folders_of_installed_programs(tmp_path, monkeypatch):
    from opensweep.core import software
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for var in ("APPDATA", "ProgramData", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.setenv(var, str(tmp_path / var))
    shared = tmp_path / "local" / "Programs" / "Python"
    (shared / "Python313").mkdir(parents=True)
    (tmp_path / "APPDATA" / "Gone App").mkdir(parents=True)
    removed = software.Program(key="k1", hive="HKCU", name="Python 3.12.1 (64-bit)")
    removed_app = software.Program(key="k3", hive="HKCU", name="Gone App 2.0")
    still = software.Program(key="k2", hive="HKCU", name="Python 3.13", install_location=str(shared / "Python313"))
    assert software.find_leftovers(removed, [still]) == []
    found = software.find_leftovers(removed_app, [still])
    assert [os.path.basename(p) for p, _ in found] == ["Gone App"]


def test_startup_approved_values():
    from opensweep.core.startup import approved_value
    on, off = approved_value(True), approved_value(False, now=1_700_000_000)
    assert len(on) == len(off) == 12
    assert on[0] == 2 and off[0] == 3


def test_listing_does_not_crash():
    from opensweep.core import software, startup
    assert isinstance(software.list_programs(with_usage=False), list)
    assert isinstance(startup.list_startup(), list)
