"""Reversible Windows integration checks on disposable, non-user startup fixtures."""
import ctypes, json, os, subprocess, sys, uuid, winreg
from pathlib import Path
from opensweep.core import startup, startup_store as store

tag = "OpenSweepTest_" + uuid.uuid4().hex[:12]
root = r"Software\OpenSweep\SelfTest_" + tag
assert root.startswith(r"Software\OpenSweep\SelfTest_OpenSweepTest_")
startup.RUN = root + r"\Run"
startup.APPROVED = root + r"\Approved"
store.SAVED = root + r"\Saved"
results = {}

def delete_tree(hive, path):
    assert path.startswith(root)
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS) as k:
            children = [winreg.EnumKey(k, n) for n in range(winreg.QueryInfoKey(k)[0])]
        for child in children:
            delete_tree(hive, path + "\\" + child)
        winreg.DeleteKey(hive, path)
    except FileNotFoundError:
        pass

try:
    command = r'"%LOCALAPPDATA%\OpenSweepTest\app.exe" --fixture'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, startup.RUN) as k:
        winreg.SetValueEx(k, tag, 0, winreg.REG_EXPAND_SZ, command)
    e = startup.StartupEntry(tag, command, "Test", "HKCU", "Run", True, reg_path=startup.RUN)
    startup.set_enabled(e, False)
    assert store._live_value(e) is None
    listed = startup._run_entries("HKCU", startup.RUN, "Run", "Test")
    assert len(listed) == 1 and not listed[0].enabled
    startup.set_enabled(listed[0], True)
    assert store._live_value(e) == (command, winreg.REG_EXPAND_SZ)
    results["registry_roundtrip"] = "passed"

    if ctypes.windll.shell32.IsUserAnAdmin():
        registered = False
        try:
            q = startup._ps_quote(tag)
            startup._powershell(
                f"$a=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit 0'; "
                f"Register-ScheduledTask -TaskName {q} -Action $a -ErrorAction Stop | Out-Null")
            registered = True
            task = startup.StartupEntry(tag, "cmd.exe", "", "SYS", "Task", True, reg_path="\\")
            startup.set_enabled(task, False)
            assert startup._powershell(f"(Get-ScheduledTask -TaskName {q}).Settings.Enabled").strip() == "False"
            startup.set_enabled(task, True)
            assert startup._powershell(f"(Get-ScheduledTask -TaskName {q}).Settings.Enabled").strip() == "True"
            results["scheduled_task_roundtrip"] = "passed"
        finally:
            if registered:
                startup._powershell(f"Unregister-ScheduledTask -TaskName {q} -Confirm:$false -ErrorAction Stop")

        created = False
        try:
            out = subprocess.run(["sc.exe", "create", tag, "binPath=",
                                  r"C:\Windows\System32\cmd.exe /c exit 0", "start=", "demand"],
                                  capture_output=True, text=True)
            assert out.returncode == 0, out.stdout + out.stderr
            created = True
            service = startup.StartupEntry(tag, "cmd.exe", "", "SYS", "Service", True, reg_path=tag)
            startup.set_enabled(service, False)
            assert store.service_state(tag)["start"] == 4
            startup.set_enabled(service, True)
            assert store.service_state(tag)["start"] == 3
            results["service_roundtrip"] = "passed"
        finally:
            if created:
                out = subprocess.run(["sc.exe", "delete", tag], capture_output=True, text=True)
                assert out.returncode == 0, out.stdout + out.stderr
                assert not store.values("HKLM", "Service")
    else:
        results["system_fixtures"] = "skipped: no elevation"
finally:
    delete_tree(winreg.HKEY_CURRENT_USER, root)
    delete_tree(winreg.HKEY_LOCAL_MACHINE, root)

print(json.dumps(results, indent=2))
