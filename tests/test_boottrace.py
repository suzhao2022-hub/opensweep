import json
from opensweep.core import boottrace as bt, startup


def proc(pid, exe, created, ppid=0, name=None):
    return {"pid": pid, "exe": exe, "create_time": created, "ppid": ppid,
            "name": name or "app.exe", "cmdline": [exe, "--test"]}


def entry(exe, enabled=True, kind="Run", name="App"):
    return startup.StartupEntry(name, exe, "", "HKCU", kind, enabled, exe=exe)


def test_multiple_sources_for_one_executable_are_retained(tmp_path):
    exe = str(tmp_path / "app.exe")
    rows = [proc(1, r"C:\Windows\System32\services.exe", 100, name="services.exe"),
            proc(2, exe, 110, 1)]
    items = bt._trace([entry(exe, False), entry(exe, True, "Service", "AppService")], rows, 100, None)
    item = next(i for i in items if i.exe == exe)
    assert item.entry == "AppService" and item.entry_on
    assert len(item.candidates) == 2
    assert {c["type"] for c in item.candidates} == {"Run", "Service"}


def test_delayed_login_and_manual_launch_are_not_silently_omitted(tmp_path):
    exe = str(tmp_path / "app.exe")
    rows = [proc(2, exe, 5000)]
    assert bt._trace([], rows, 100, None)[0].seconds == 4900
    assert bt._trace([], rows, 100, bt.WINDOW) == []


def test_reused_parent_pid_does_not_invent_a_launcher(tmp_path):
    child, parent = str(tmp_path / "child.exe"), str(tmp_path / "parent.exe")
    rows = [proc(1, parent, 200), proc(2, child, 110, 1)]
    items = bt._trace([entry(parent)], rows, 100, None)
    assert next(i for i in items if i.exe == child).verdict == bt.OTHER


def test_parent_cycles_terminate(tmp_path):
    rows = [proc(1, str(tmp_path / "a.exe"), 110, 2),
            proc(2, str(tmp_path / "b.exe"), 110, 1)]
    assert len(bt._trace([], rows, 100, None)) == 2


def test_shared_install_folder_is_not_launch_proof(tmp_path):
    exe = str(tmp_path / "App" / "main.exe")
    helper = str(tmp_path / "App" / "helper.exe")
    item = bt._trace([entry(exe)], [proc(2, helper, 110)], 100, None)[0]
    assert item.verdict == bt.OTHER and not item.candidates


def test_report_contains_version_settings_parent_and_command(tmp_path):
    exe = str(tmp_path / "app.exe")
    rows = [proc(2, exe, 110, 99)]
    entries = [entry(exe, False)]
    report = bt.BootReport(150, 100, bt._trace(entries, rows, 100, None), entries, rows,
                           {"history": [], "watched": 1})
    data = json.loads(report.to_json())
    assert data["version"] == "1.0.1"
    assert data["items"][0]["ppid"] == 99
    assert data["items"][0]["command"] == [exe, "--test"]
    assert data["startup_entries"][0]["enabled"] is False
    assert "not a historical boot log" in data["scope"]


def test_no_false_service_verdict_for_task_scheduler_host(tmp_path):
    exe = str(tmp_path / "app.exe")
    rows = [proc(1, "", 100, name="svchost.exe"), proc(2, exe, 110, 1)]
    assert bt._trace([], rows, 100, None)[0].verdict == bt.OTHER
