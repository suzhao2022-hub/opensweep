"""Reversible storage for startup entries, outside Windows' startup locations.

Backups stay in the same registry hive or next to the original Startup folder,
so all-users changes retain their administrator boundary.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from opensweep.core.platform import IS_WINDOWS

if IS_WINDOWS:
    import winreg

SAVED = r"Software\OpenSweep\DisabledStartup"
FOLDER_NAME = "OpenSweep Disabled Startup"


def _root(hive):
    return winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def values(hive, kind):
    try:
        key = winreg.OpenKey(_root(hive), SAVED + "\\" + kind)
    except FileNotFoundError:
        return {}
    with key:
        return {name: (value, typ) for name, value, typ in
                (winreg.EnumValue(key, i) for i in range(winreg.QueryInfoKey(key)[1]))}


def _save(hive, kind, name, value, typ):
    with winreg.CreateKeyEx(_root(hive), SAVED + "\\" + kind, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, typ, value)


def _forget(hive, kind, name):
    try:
        with winreg.OpenKey(_root(hive), SAVED + "\\" + kind, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _live_value(entry):
    try:
        with winreg.OpenKey(_root(entry.hive), entry.reg_path) as key:
            return winreg.QueryValueEx(key, entry.name)
    except FileNotFoundError:
        return None


def _saved_value(entry):
    return next((v for k, v in values(entry.hive, entry.approved_key).items()
                 if k.casefold() == entry.name.casefold()), None)


def set_run_enabled(entry, enabled, write_approved):
    live = _live_value(entry)
    saved = _saved_value(entry)
    if live is None and saved is None:
        raise FileNotFoundError(entry.name)
    if enabled:
        value, typ = live if live is not None else saved
        write_approved(entry, True)
        if live is None:
            with winreg.CreateKeyEx(_root(entry.hive), entry.reg_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, entry.name, 0, typ, value)
        if _live_value(entry) != (value, typ):
            raise OSError("Startup command could not be restored: " + entry.name)
        _forget(entry.hive, entry.approved_key, entry.name)
    else:
        if live is not None:
            # Preserve the exact value and REG_EXPAND_SZ type before any mutation.
            _save(entry.hive, entry.approved_key, entry.name, live[0], live[1])
        write_approved(entry, False)
        if live is not None:
            with winreg.OpenKey(_root(entry.hive), entry.reg_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, entry.name)
        if _live_value(entry) is not None:
            raise OSError("Startup command is still present: " + entry.name)


def folder_backup(path):
    original = Path(path)
    return original.parent.parent / FOLDER_NAME / original.name


def set_folder_enabled(entry, enabled, write_approved):
    original = Path(entry.reg_path)
    saved = folder_backup(original)
    if enabled:
        if saved.exists():
            if original.exists():
                raise FileExistsError("Both startup copies exist: " + str(original))
            write_approved(entry, True)
            # Same volume, atomic rename. Never overwrite an independently recreated entry.
            saved.rename(original)
        elif original.exists():
            write_approved(entry, True)
        else:
            raise FileNotFoundError(str(original))
        if not original.is_file():
            raise OSError("Startup shortcut could not be restored: " + str(original))
    else:
        if original.exists():
            saved.parent.mkdir(parents=True, exist_ok=True)
            write_approved(entry, False)
            os.replace(original, saved)
        elif not saved.is_file():
            raise FileNotFoundError(str(original))
        else:
            write_approved(entry, False)
        if original.exists() or not saved.is_file():
            raise OSError("Startup shortcut is still active: " + str(original))


def remove_run(entry):
    # A disabled entry is in our backup key, not the Run key.
    if _live_value(entry) is not None:
        with winreg.OpenKey(_root(entry.hive), entry.reg_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, entry.name)
    _forget(entry.hive, entry.approved_key, entry.name)


def service_state(name):
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Services" + "\\" + name) as key:
        start = winreg.QueryValueEx(key, "Start")[0]
        try:
            delayed = winreg.QueryValueEx(key, "DelayedAutoStart")[0]
        except FileNotFoundError:
            delayed = 0
    return {"start": start, "delayed": delayed}


def service_original(entry, save=False):
    saved = next((v for k, v in values("HKLM", "Service").items()
                  if k.casefold() == entry.reg_path.casefold()), None)
    if saved:
        state = json.loads(saved[0])
        if not isinstance(state, dict) or state.get("start") not in (2, 3, 4):
            raise ValueError("Invalid saved service startup mode")
        return state
    state = service_state(entry.reg_path)
    if save:
        _save("HKLM", "Service", entry.reg_path, json.dumps(state), winreg.REG_SZ)
    return state


def restore_service_delay(name, state):
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Services" + "\\" + name, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "DelayedAutoStart", 0, winreg.REG_DWORD, state.get("delayed", 0))


def forget_service(entry):
    _forget("HKLM", "Service", entry.reg_path)

