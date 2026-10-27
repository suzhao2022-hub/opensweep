"""Windows startup entries with reversible, verified disable operations.

Run commands and Startup files are backed up outside their launch locations.
Tasks use Settings.Enabled; services use Disabled and restore their original mode.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache

from opensweep.core.platform import IS_WINDOWS, config_dir, resource_path
from opensweep.core import startup_store

if IS_WINDOWS:
    import winreg

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
RUNONCE = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce"
POLICY_RUN = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run"
STORE_TASKS = (r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion"
               r"\AppModel\SystemAppData")
WINLOGON_USER = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
RESTART_APPS = "RestartApps"
# Launch points Explorer's StartupApproved flags do not cover: switching them off only removes the value.
NO_APPROVAL = {"RunOnce", "RunOnce32", "PolicyRun"}
# Settings rather than programs: no command line to resolve.
NO_EXE = {"StoreApp", "RestartApps"}
GUARD_VALUE = "OpenSweep Guard"  # OpenSweep's own once-at-logon check (see core/guard.py)
LAUNCHERS = {"wscript.exe", "cscript.exe", "rundll32.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "mshta.exe"}


@dataclass
class StartupEntry:
    name: str
    command: str
    source: str        # human readable location
    hive: str          # HKCU | HKLM
    approved_key: str  # StartupApproved subkey: Run | Run32 | StartupFolder
    enabled: bool
    exe: str = ""            # program that actually starts
    exists: bool = True      # False when that program is gone
    description: str = ""
    company: str = ""
    kind: str = ""           # see data/startup_advice.json
    advice: str = "optional"  # keep | optional | disable
    reg_path: str = ""       # Run key path, or the shortcut file for StartupFolder entries
    extra: dict = field(default_factory=dict)

    @property
    def needs_admin(self) -> bool:
        return self.hive in ("HKLM", "SYS")

    @property
    def removable(self) -> bool:
        """Broken Run/Startup-folder entries can be deleted; tasks and services are only switched off."""
        return self.approved_key not in ("Task", "Service") + tuple(NO_EXE)

    @property
    def display_name(self) -> str:
        if self.description and len(self.description) <= 40:
            return self.description
        name = self.name
        return name[:-4] if name.lower().endswith(".lnk") else name


def _root(hive: str):
    return winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def _approved_state(hive: str, sub: str, name: str) -> bool:
    try:
        with winreg.OpenKey(_root(hive), f"{APPROVED}\\{sub}") as key:
            data, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return True  # no flag means enabled
    return not (isinstance(data, bytes) and data and data[0] & 1)


def _run_entries(hive: str, path: str, approved: str, label: str) -> list[StartupEntry]:
    entries = []
    try:
        key = winreg.OpenKey(_root(hive), path)
    except FileNotFoundError:
        key = None
    if key is not None:
        with key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    continue
                entries.append(StartupEntry(name, str(value), label, hive, approved,
                                            _approved_state(hive, approved, name), reg_path=path))
                entries[-1].extra["needs_block"] = not entries[-1].enabled
    present = {e.name.casefold() for e in entries}
    for name, (value, typ) in startup_store.values(hive, approved).items():
        if name.casefold() not in present:
            e = StartupEntry(name, str(value), label, hive, approved, False, reg_path=path)
            e.extra["stored"] = True
            entries.append(e)
    return entries


def _folder_entries(folder: str, hive: str, label: str) -> list[StartupEntry]:
    entries = []
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower() != "desktop.ini":
                entries.append(StartupEntry(entry.name, entry.path, label, hive, "StartupFolder",
                                            _approved_state(hive, "StartupFolder", entry.name),
                                            reg_path=entry.path))
    except FileNotFoundError:
        pass
    for e in entries:
        e.extra["needs_block"] = not e.enabled
    present = {e.name.casefold() for e in entries}
    saved_folder = startup_store.folder_backup(os.path.join(folder, "_")).parent
    try:
        for saved in saved_folder.iterdir():
            if saved.is_file() and saved.name.casefold() not in present:
                original = os.path.join(folder, saved.name)
                e = StartupEntry(saved.name, original, label, hive, "StartupFolder", False,
                                 reg_path=original)
                e.extra["stored_path"] = str(saved)
                entries.append(e)
    except FileNotFoundError:
        pass
    return entries


def _store_state(path: str) -> int | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return int(winreg.QueryValueEx(key, "State")[0])
    except (OSError, ValueError, TypeError):
        return None


def _subkeys(key) -> list[str]:
    names = []
    for i in range(winreg.QueryInfoKey(key)[0]):
        try:
            names.append(winreg.EnumKey(key, i))
        except OSError:
            continue
    return names


def _store_entries() -> list[StartupEntry]:
    """Store (UWP) apps' startup tasks, the ones Task Manager lists. State: 2/4 on, 0/1/3 off; 3/4 are policy."""
    entries = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STORE_TASKS)
    except OSError:
        return entries
    with root:
        for package in _subkeys(root):
            try:
                with winreg.OpenKey(root, package) as pkg:
                    tasks = _subkeys(pkg)
            except OSError:
                continue
            for task in tasks:
                path = f"{STORE_TASKS}\\{package}\\{task}"
                state = _store_state(path)
                if state is None:
                    continue
                e = StartupEntry(package.split("_")[0], package, "Store app", "HKCU", "StoreApp",
                                 state in (2, 4), reg_path=path)
                e.extra.update(task=task, state=state)
                entries.append(e)
    return entries


def _restart_apps_value() -> int | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINLOGON_USER) as key:
            return int(winreg.QueryValueEx(key, RESTART_APPS)[0])
    except (OSError, ValueError, TypeError):
        return None


def _restart_entries() -> list[StartupEntry]:
    """Windows reopening the apps that were open at restart/sign-out ("Restart apps" in Sign-in options).

    Nothing in Run keys or tasks shows this, yet it relaunches programs whose startup entry is off.
    When the value was never written, Windows 11 22H2+ treats it as on for many accounts."""
    value = _restart_apps_value()
    default = value is None
    if default and sys.getwindowsversion().build < 22621:
        return []
    e = StartupEntry("Reopen apps after sign-in", "Windows: reopen the apps that were open at restart",
                     "Windows setting", "HKCU", "RestartApps", value == 1 or default, reg_path=WINLOGON_USER)
    e.extra["default"] = default
    return [e]


def _set_store_enabled(entry: StartupEntry, enabled: bool) -> None:
    state = _store_state(entry.reg_path)
    if state in (3, 4):
        raise OSError("Startup of this app is controlled by policy: " + entry.name)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, entry.reg_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "State", 0, winreg.REG_DWORD, 2 if enabled else 1)
    if (_store_state(entry.reg_path) in (2, 4)) != enabled:
        raise OSError("Store app startup setting did not change: " + entry.name)
    entry.extra["state"] = 2 if enabled else 1


def _set_restart_enabled(entry: StartupEntry, enabled: bool) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINLOGON_USER, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RESTART_APPS, 0, winreg.REG_DWORD, int(enabled))
    if _restart_apps_value() != int(enabled):
        raise OSError("Restart apps setting did not change")
    entry.extra["default"] = False


def launch_points() -> list[dict]:
    """Other places that start programs, for the diagnostic report only. OpenSweep never edits these:
    a wrong Winlogon value can stop Windows from signing in."""
    if not IS_WINDOWS:
        return []
    found = []

    def value(hive, path, name):
        try:
            with winreg.OpenKey(hive, path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    winlogon = r"Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
    for name, normal in (("Userinit", "userinit.exe"), ("Shell", "explorer.exe")):
        data = value(winreg.HKEY_LOCAL_MACHINE, winlogon, name)
        if data is not None:
            parts = [p.strip() for p in str(data).split(",") if p.strip()]
            found.append({"location": "HKLM Winlogon", "name": name, "value": str(data),
                          "unusual": any(os.path.basename(p).lower() != normal for p in parts)})
    for hive, label, path, name in (
            (winreg.HKEY_LOCAL_MACHINE, "HKLM Windows", r"Software\Microsoft\Windows NT\CurrentVersion\Windows",
             "AppInit_DLLs"),
            (winreg.HKEY_CURRENT_USER, "HKCU Environment", "Environment", "UserInitMprLogonScript")):
        data = value(hive, path, name)
        if data:
            found.append({"location": label, "name": name, "value": str(data), "unusual": True})
    restart = _restart_apps_value()
    found.append({"location": "HKCU Winlogon", "name": RESTART_APPS,
                  "value": "not set" if restart is None else str(restart), "unusual": restart == 1,
                  "windows_build": sys.getwindowsversion().build})
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower()
    active = r"Software\Microsoft\Active Setup\Installed Components"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, active) as root:
            for guid in _subkeys(root):
                stub = value(winreg.HKEY_LOCAL_MACHINE, f"{active}\\{guid}", "StubPath")
                if stub and windir not in os.path.expandvars(str(stub)).lower():
                    found.append({"location": "HKLM Active Setup", "name": guid, "value": str(stub),
                                  "unusual": False})
    except OSError:
        pass
    return found


# ----------------------------------------------------------------- resolving


def resolve_shortcuts(paths: list[str]) -> dict[str, str]:
    """Target of each .lnk file (via the Windows Script Host, in one PowerShell call)."""
    paths = [p for p in paths if p.lower().endswith(".lnk")]
    if not IS_WINDOWS or not paths:
        return {}
    script = ("$s = New-Object -ComObject WScript.Shell; "
              "foreach ($p in $input) { try { $s.CreateShortcut($p).TargetPath } catch { '' } }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             input="\n".join(paths), capture_output=True, text=True, encoding="utf-8",
                             timeout=20, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError):
        return {}
    targets = out.stdout.splitlines()
    return {p: (targets[i].strip() if i < len(targets) else "") for i, p in enumerate(paths)}


def command_exe(command: str) -> str:
    """The program a startup command runs. For script hosts (wscript, rundll32...) that is the host."""
    from opensweep.core.software import _split_command
    parts = _split_command(os.path.expandvars(command.strip()))
    exe = parts[0].strip().strip('"') if parts else ""
    if not exe:
        return ""
    if not os.path.isabs(exe):
        found = shutil.which(exe) or shutil.which(exe + ".exe")
        if not found:
            system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", exe)
            found = system32 if os.path.exists(system32) else ""
        return found or exe
    return exe


def _launched_file(command: str) -> str:
    """The script or DLL a script host is asked to run ('' if none is named)."""
    from opensweep.core.software import _split_command
    for arg in _split_command(os.path.expandvars(command))[1:]:
        arg = arg.strip().strip('"').split(",")[0]
        if re.search(r"\.(vbs|vbe|js|jse|wsf|ps1|bat|cmd|dll|hta)$", arg, re.IGNORECASE):
            return arg
    return ""


@lru_cache(maxsize=1)
def _advice_data() -> dict:
    try:
        return json.loads(resource_path("data", "startup_advice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"advice": {}, "groups": []}


def _words(text: str) -> tuple[str, set[str]]:
    low = text.lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff.]", "", low), set(re.findall(r"[0-9a-z\u4e00-\u9fff]+", low))


def classify(*texts: str) -> tuple[str, str]:
    """(kind, advice) for an app, from its name, exe and description."""
    joined, words = "", set()
    for text in texts:
        j, w = _words(text or "")
        joined += " " + j
        words |= w
    data = _advice_data()
    for group in data.get("groups", []):
        for pattern in group["match"]:
            p = pattern.lower()
            if (p[1:] in words) if p.startswith("=") else (p in joined):
                return group["kind"], data["advice"].get(group["kind"], "optional")
    return "", "optional"


def list_startup(include_system: bool = True, errors: list[str] | None = None) -> list[StartupEntry]:
    """Run keys and Startup folders; with `include_system` also scheduled tasks and services
    from other vendors that start programs at logon/boot."""
    if not IS_WINDOWS:
        return []
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("ProgramData", "")
    entries = (
        _run_entries("HKCU", RUN, "Run", "Registry (user)")
        + _run_entries("HKLM", RUN, "Run", "Registry (all users)")
        + _run_entries("HKLM", RUN32, "Run32", "Registry (all users, 32-bit)")
        + _run_entries("HKCU", RUN32, "Run32", "Registry (user, 32-bit)")
        + _folder_entries(os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup"),
                          "HKCU", "Startup folder (user)")
        + _folder_entries(os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs\StartUp"),
                          "HKLM", "Startup folder (all users)")
        + _run_entries("HKCU", RUNONCE, "RunOnce", "Registry (user, once)")
        + _run_entries("HKLM", RUNONCE, "RunOnce", "Registry (all users, once)")
        + _run_entries("HKLM", RUNONCE32, "RunOnce32", "Registry (all users, 32-bit, once)")
        + _run_entries("HKCU", POLICY_RUN, "PolicyRun", "Registry (user, policy)")
        + _run_entries("HKLM", POLICY_RUN, "PolicyRun", "Registry (all users, policy)")
        + _store_entries()
        + _restart_entries()
    )
    if include_system:
        entries += _system_entries(errors)
    return sorted(entries, key=lambda e: e.name.lower())


# ----------------------------------------------------------------- tasks and services

_SYSTEM_QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$tasks = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
  $trig = @($_.Triggers | ForEach-Object { $_.CimClass.CimClassName })
  $act = @($_.Actions | Where-Object { $_.Execute } | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() })
  [pscustomobject]@{ kind='task'; name=$_.TaskName; path=$_.TaskPath; state=[string]$_.State;
                     enabled=[bool]$_.Settings.Enabled; triggers=($trig -join ','); command=($act -join ' | ') }
}
$services = Get-CimInstance Win32_Service | Where-Object { $_.PathName } |
  ForEach-Object { [pscustomobject]@{ kind='service'; name=$_.Name; display=$_.DisplayName; state=$_.State;
                                      start_mode=$_.StartMode; command=$_.PathName } }
@($tasks) + @($services) | ConvertTo-Json -Compress -Depth 3
"""

# Triggers that make a task run without the user doing anything.
AUTO_TRIGGERS = ("Logon", "Boot", "Daily", "Time", "Idle", "SessionStateChange", "Registration")


def _powershell(script: str, timeout: int = 40) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout, creationflags=0x08000000)
    if out.returncode != 0:
        detail = out.stderr.strip() or out.stdout.strip() or f"PowerShell exited with code {out.returncode}"
        raise OSError(detail.splitlines()[-1])
    return out.stdout


def _system_entries(errors: list[str] | None = None) -> list[StartupEntry]:
    try:
        raw = _powershell(_SYSTEM_QUERY).strip()
        items = json.loads(raw) if raw else []
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if errors is not None:
            errors.append("Tasks/services could not be read: " + str(exc))
        return []
    if isinstance(items, dict):
        items = [items]
    from opensweep.core import guard
    watched = guard.watched_services() | {n.lower() for n in startup_store.values("HKLM", "Service")}
    return parse_system_entries(items, watched)


def parse_system_entries(items: list[dict], watched_services: set[str] = frozenset()) -> list[StartupEntry]:
    """Turn the PowerShell output into entries, keeping only non-Microsoft things that start by themselves."""
    from opensweep.core.software import exe_company
    entries = []
    for item in items:
        command = (item.get("command") or "").split(" | ")[0]
        if item.get("kind") == "task":
            triggers = item.get("triggers") or ""
            if not any(t in triggers for t in AUTO_TRIGGERS) or not command:
                continue
            # A disabled task that still has a running copy reports state "Running", so trust the
            # task's own Enabled switch when we have it.
            enabled = item["enabled"] if "enabled" in item else (item.get("state") or "") != "Disabled"
            e = StartupEntry(item["name"], command, "Task", "SYS", "Task", bool(enabled),
                             reg_path=item.get("path") or "\\")
            e.extra["triggers"] = triggers
            e.extra["commands"] = (item.get("command") or "").split(" | ")
        else:
            mode = item.get("start_mode") or "Auto"
            if mode == "Manual" and (item.get("name") or "").lower() not in watched_services:
                continue
            exe = command_exe(command)
            low = exe.lower()
            if low.endswith("svchost.exe") or "\\windows\\" in low and "\\system32\\" in low:
                continue  # Windows' own services
            name = (item.get("name") or "").lower()
            if "microsoft" in exe_company(exe).lower() and "edgeupdate" not in name:
                continue  # Microsoft's own services are part of Windows; only Edge's updater is optional
            e = StartupEntry(item.get("display") or item["name"], command, "Service", "SYS", "Service",
                             mode != "Disabled", reg_path=item["name"])
            e.extra["start_mode"] = mode
            e.extra["running"] = item.get("state") == "Running"
        entries.append(e)
    return entries


def _set_system_enabled(entry: StartupEntry, enabled: bool) -> None:
    if entry.approved_key == "Task":
        verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
        args = f"-TaskName {_ps_quote(entry.name)} -TaskPath {_ps_quote(entry.reg_path)} -ErrorAction Stop"
        wanted = "$true" if enabled else "$false"
        script = (f"{verb} {args} | Out-Null; "
                  f"if ((Get-ScheduledTask {args}).Settings.Enabled -ne {wanted}) "
                  "{ throw 'Task startup setting did not change' }")
    else:
        original = startup_store.service_original(entry, save=not enabled)
        mode = ("Manual" if original["start"] == 3 else "Automatic") if enabled else "Disabled"
        script = f"Set-Service -Name {_ps_quote(entry.reg_path)} -StartupType {mode} -ErrorAction Stop"
    try:
        _powershell(script, timeout=30)
        if entry.approved_key == "Service":
            expected = (3 if mode == "Manual" else 2) if enabled else 4
            if startup_store.service_state(entry.reg_path)["start"] != expected:
                raise OSError("Service startup setting did not change: " + entry.reg_path)
            if enabled:
                startup_store.restore_service_delay(entry.reg_path, original)
                startup_store.forget_service(entry)
            entry.extra["start_mode"] = mode
    except OSError as exc:
        if "denied" in str(exc).lower() or "拒绝" in str(exc) or "0x80070005" in str(exc):
            raise PermissionError(str(exc)) from exc
        raise


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def describe(entries: list[StartupEntry]) -> list[StartupEntry]:
    """Fill in exe, description, publisher, advice and whether the program still exists."""
    from opensweep.core.software import exe_description, exe_company
    targets = resolve_shortcuts([e.extra.get("stored_path", e.command) for e in entries
                                 if e.approved_key == "StartupFolder"])
    for e in entries:
        if e.approved_key == "StartupFolder":
            actual = e.extra.get("stored_path", e.command)
            target = targets.get(actual, "")
            e.exe = target or (actual if not actual.lower().endswith(".lnk") else "")
        elif e.approved_key in NO_EXE:
            e.exe = ""
        else:
            e.exe = command_exe(e.command)
        if os.path.basename(e.exe).lower() in LAUNCHERS:
            # A script host (wscript, rundll32...): what matters is the script it runs.
            script = _launched_file(e.command)
            e.extra["host"] = e.exe
            e.exe = script
            if script and os.path.isabs(script):
                e.exists = os.path.exists(script)
        elif e.exe and os.path.isabs(e.exe):
            e.exists = os.path.exists(e.exe)
        if e.exists and e.exe.lower().endswith(".exe"):
            e.description = exe_description(e.exe)
            e.company = exe_company(e.exe)
        e.kind, e.advice = classify(e.name, os.path.basename(e.exe), e.description, e.company)
        if e.name == GUARD_VALUE:
            e.kind, e.advice, e.description = "opensweep", "keep", ""
    return entries


# ----------------------------------------------------------------- changing


def approved_value(enabled: bool, now: float | None = None) -> bytes:
    if enabled:
        return bytes([2]) + bytes(11)
    filetime = int(((now or time.time()) + 11644473600) * 10_000_000)
    return bytes([3, 0, 0, 0]) + struct.pack("<Q", filetime)


def set_enabled(entry: StartupEntry, enabled: bool) -> None:
    """Raises PermissionError for all-users entries when not elevated."""
    if entry.approved_key in ("Task", "Service"):
        _set_system_enabled(entry, enabled)
        entry.enabled = enabled
        return
    if entry.approved_key in NO_EXE:
        (_set_store_enabled if entry.approved_key == "StoreApp" else _set_restart_enabled)(entry, enabled)
        entry.enabled = enabled
        return
    if entry.approved_key == "StartupFolder":
        startup_store.set_folder_enabled(entry, enabled, _write_approved)
    else:
        startup_store.set_run_enabled(entry, enabled, _write_approved)
    entry.enabled = enabled
    entry.extra["needs_block"] = False
    if entry.approved_key == "StartupFolder":
        if enabled:
            entry.extra.pop("stored_path", None)
        else:
            entry.extra["stored_path"] = str(startup_store.folder_backup(entry.reg_path))


def _write_approved(entry: StartupEntry, enabled: bool) -> None:
    if entry.approved_key in NO_APPROVAL:
        return  # no StartupApproved flag exists for these; the backup move is the switch
    with winreg.CreateKeyEx(_root(entry.hive), f"{APPROVED}\\{entry.approved_key}", 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, entry.name, 0, winreg.REG_BINARY, approved_value(enabled))
    if _approved_state(entry.hive, entry.approved_key, entry.name) != enabled:
        raise OSError("Startup approval setting did not change: " + entry.name)


@dataclass
class ChangeResult:
    done: list[StartupEntry]
    failed: list[tuple[StartupEntry, str]]
    needs_admin: bool = False


def apply_changes(enable: list[StartupEntry] = (), disable: list[StartupEntry] = (),
                  remove_entries: list[StartupEntry] = ()) -> ChangeResult:
    """Apply several startup changes (slow for tasks and services, so run this off the UI thread)."""
    result = ChangeResult([], [])
    jobs = [(e, "on") for e in enable] + [(e, "off") for e in disable] + [(e, "rm") for e in remove_entries]
    for entry, job in jobs:
        try:
            if job == "rm":
                remove(entry)
            else:
                set_enabled(entry, job == "on")
            result.done.append(entry)
        except PermissionError as exc:
            result.needs_admin = True
            result.failed.append((entry, str(exc)))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result.failed.append((entry, str(exc)))
    # The startup guard keeps what the user switched off switched off.
    from opensweep.core import guard
    done = {id(e) for e in result.done}
    guard.remember([e for e in disable if id(e) in done])
    guard.forget([e for e in list(enable) + list(remove_entries) if id(e) in done])
    return result


def remove(entry: StartupEntry) -> None:
    """Delete a startup entry (used for entries whose program is gone). Backed up first."""
    backup = config_dir() / "startup-removed.json"
    try:
        saved = json.loads(backup.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = []
    saved.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "name": entry.name, "command": entry.command,
                  "hive": entry.hive, "key": entry.reg_path, "type": entry.approved_key})
    backup.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    if entry.approved_key == "StartupFolder":
        from send2trash import send2trash
        original = entry.reg_path
        stored = str(startup_store.folder_backup(original))
        if os.path.exists(original):
            send2trash(original)
        if os.path.exists(stored):
            send2trash(stored)
    else:
        startup_store.remove_run(entry)
    try:
        with winreg.OpenKey(_root(entry.hive), f"{APPROVED}\\{entry.approved_key}", 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, entry.name)
    except OSError:
        pass
