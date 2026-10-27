import os
import time
from pathlib import Path

import pytest

from opensweep.core import appscan, software


def _file(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(appscan, "MIN_PORTABLE", 1)
    monkeypatch.setattr(appscan, "MIN_LEFTOVER", 1)
    monkeypatch.setattr(appscan, "_running_dirs", lambda: [])
    monkeypatch.setattr(appscan, "write_log", lambda entries: None)
    monkeypatch.setattr(software, "read_userassist", lambda: {})
    monkeypatch.setattr(software, "prefetch_times", lambda: {})
    monkeypatch.setattr(software, "service_dirs", lambda: [])
    monkeypatch.setattr(software, "exe_description", lambda path: "")

    pf, drive = tmp_path / "PF", tmp_path / "D"
    _file(pf / "OldApp" / "config.ini")                       # leftover: no program left
    _file(pf / "Tool" / "tool.exe")
    _file(pf / "Tool" / "core.dll")
    _file(drive / "Photos" / "a.jpg")                         # user data: never listed
    _file(drive / "GreenApp" / "green.exe")
    _file(drive / "GreenApp" / "lib.dll")
    _file(drive / "Installers" / "QQ_9.9.exe")                # installers only, no dll: data
    _file(drive / "MyProj" / ".git" / "HEAD")                 # source project
    _file(drive / "MyProj" / "build" / "app.exe")
    _file(drive / "MyProj" / "build" / "x.dll")
    _file(drive / "Registered" / "reg.exe")
    _file(drive / "Registered" / "reg.dll")
    _file(drive / "Bundle" / "GameA" / "a.exe")               # several programs: listed separately
    _file(drive / "Bundle" / "GameA" / "a.dll")
    _file(drive / "Bundle" / "GameB" / "b.exe")
    _file(drive / "Bundle" / "GameB" / "b.dll")
    _file(drive / "Studio" / "bin" / "studio.exe")            # one program with bin/ and jbr/
    _file(drive / "Studio" / "lib" / "x.dll")
    _file(drive / "Studio" / "jbr" / "bin" / "java.exe")
    _file(drive / "My Downloads" / "tool" / "t.exe")          # downloads are skipped by name
    _file(drive / "My Downloads" / "tool" / "t.dll")
    programs = [software.Program(key="k", hive="HKCU", name="Registered App",
                                 install_location=str(drive / "Registered"))]
    roots = [(str(pf), True), (str(drive), False)]
    return programs, roots, pf, drive


def test_finds_portable_programs_and_leftovers_only(env):
    programs, roots, pf, drive = env
    apps = appscan.find_folder_apps(programs, roots)
    found = {os.path.relpath(a.path, str(pf.parent)): a.kind for a in apps}
    assert found == {
        os.path.join("PF", "OldApp"): "leftover",
        os.path.join("PF", "Tool"): "portable",
        os.path.join("D", "GreenApp"): "portable",
        os.path.join("D", "Bundle", "GameA"): "portable",
        os.path.join("D", "Bundle", "GameB"): "portable",
        os.path.join("D", "Studio"): "portable",
    }
    studio = next(a for a in apps if a.name == "Studio")
    assert os.path.basename(studio.main_exe) == "studio.exe"


def test_nameless_registered_program_is_matched_by_name(env):
    programs, roots, pf, drive = env
    programs = programs + [software.Program(key="k2", hive="HKLM", name="GreenApp 2.1 (x64)")]
    names = {a.name for a in appscan.find_folder_apps(programs, roots)}
    assert "GreenApp" not in names


def test_last_used_from_userassist(env, monkeypatch):
    programs, roots, pf, drive = env
    exe = os.path.normcase(str(drive / "GreenApp" / "green.exe"))
    monkeypatch.setattr(software, "read_userassist", lambda: {exe: (1_700_000_000.0, 3)})
    apps = {a.name: a for a in appscan.find_folder_apps(programs, roots)}
    assert apps["GreenApp"].last_used == 1_700_000_000.0
    assert apps["Tool"].last_used is None


def test_delete_folder_app(env):
    programs, roots, pf, drive = env
    app = next(a for a in appscan.find_folder_apps(programs, roots) if a.name == "GreenApp")
    result = appscan.delete_folder_app(app, recycle=False)
    assert result.deleted == 2 and not (drive / "GreenApp").exists()


def test_running_program_is_not_deleted(env, monkeypatch):
    programs, roots, pf, drive = env
    app = next(a for a in appscan.find_folder_apps(programs, roots) if a.name == "Tool")
    monkeypatch.setattr(appscan, "_running_dirs", lambda: [(os.path.normcase(app.path), "tool.exe")])
    with pytest.raises(RuntimeError):
        appscan.delete_folder_app(app, recycle=False)
    assert (pf / "Tool").exists()


def test_usage_verdict():
    now = time.time()
    p = software.Program(key="k", hive="HKCU", name="x")
    p.last_used = now - 200 * 86400
    assert software.usage_verdict(p, now) == ("unused", 200)
    p.last_used = now - 3 * 86400
    assert software.usage_verdict(p, now)[0] == "recent"
    p.last_used, p.install_date = None, now - 5 * 86400
    assert software.usage_verdict(p, now)[0] == "new"
    p.install_date = None
    assert software.usage_verdict(p, now)[0] == "no_record"
