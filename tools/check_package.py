"""Check built executables without touching the user's startup configuration.

Run: python tools/check_package.py dist/OpenSweep
The GUI guard-off smoke uses an isolated APPDATA and exits before guard sync.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

parser=argparse.ArgumentParser()
parser.add_argument("folder",type=Path)
args=parser.parse_args()
folder=args.folder.resolve()
cli=folder/"opensweep-cli.exe"
gui=folder/"OpenSweep.exe"
assert cli.is_file() and gui.is_file(), "Missing executable"
version=subprocess.check_output([str(cli),"--version"],text=True,encoding="utf-8",timeout=15).strip()
with tempfile.TemporaryDirectory(prefix="opensweep-package-check-") as temp:
    profile=Path(temp)/"OpenSweep";profile.mkdir()
    (profile/"settings.json").write_text('{"guard":false,"check_updates":false}',encoding="utf-8")
    env={**os.environ,"APPDATA":temp}
    process=subprocess.Popen([str(gui),"--guard"],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        out,err=process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("Frozen GUI entry point did not exit; check for an exception dialog")
    assert process.returncode==0,(out,err)
    result=subprocess.run([str(cli),"startup-report"],env=env,capture_output=True,text=True,
                          encoding="utf-8",timeout=60,check=True)
    report=json.loads(result.stdout)
    assert report["version"]==version
    assert report["schema"]==1 and "startup_entries" in report
    assert "processes" in report and "scope" in report
    assert not report["errors"], report["errors"]
print(json.dumps({"version":version,"gui_guard_off":"passed","cli_diagnostic":"passed",
                  "entries":len(report["startup_entries"]),"processes":len(report["processes"])},indent=2))
