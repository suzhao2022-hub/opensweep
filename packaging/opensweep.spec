# PyInstaller spec: builds dist/OpenSweep/ with OpenSweep.exe (GUI) and opensweep-cli.exe (console),
# sharing one set of libraries. Run from the repository root:
#   pyinstaller packaging/opensweep.spec --noconfirm
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "opensweep" / "__init__.py").read_text()).group(1)
NUMS = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])


def version_info(filename):
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=NUMS, prodvers=NUMS),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "OpenSweep contributors"),
                StringStruct("FileDescription", "OpenSweep disk cleaner"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "OpenSweep"),
                StringStruct("LegalCopyright", "MIT License"),
                StringStruct("OriginalFilename", filename),
                StringStruct("ProductName", "OpenSweep"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


datas = [(str(SRC / "opensweep" / d), f"opensweep/{d}") for d in ("rules", "locales", "data")]
# Qt modules OpenSweep never uses: leaving them out keeps the download small.
excludes = ["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
            "PySide6.QtCharts", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtOpenGL", "PySide6.QtSvg"]
icon = str(ROOT / "packaging" / "opensweep.ico")

gui_a = Analysis([str(ROOT / "packaging" / "launcher_gui.py")], pathex=[str(SRC)], datas=datas, excludes=excludes)
cli_a = Analysis([str(ROOT / "packaging" / "launcher_cli.py")], pathex=[str(SRC)], datas=datas,
                 excludes=excludes + ["PySide6"])

# Qt's unversioned ICU imports use Windows' own ICU API. A developer PATH can
# contain an unrelated icuuc.dll (for example Poppler's), whose exports differ.
# Let Windows resolve these system DLLs instead of shipping a namesake from PATH.
system_icu = {"icu.dll", "icuin.dll", "icuuc.dll"}
foreign_icu_dirs = {Path(b[1]).parent for a in (gui_a, cli_a) for b in a.binaries
                    if Path(b[0]).name.lower() in system_icu}
for analysis in (gui_a, cli_a):
    analysis.binaries = [b for b in analysis.binaries
                         if Path(b[0]).name.lower() not in system_icu
                         and not (Path(b[1]).parent in foreign_icu_dirs
                                  and Path(b[0]).name.lower().startswith("icudt"))]

gui_exe = EXE(PYZ(gui_a.pure), gui_a.scripts, [], exclude_binaries=True, name="OpenSweep", console=False,
              icon=icon, version=version_info("OpenSweep.exe"), upx=False)
cli_exe = EXE(PYZ(cli_a.pure), cli_a.scripts, [], exclude_binaries=True, name="opensweep-cli", console=True,
              icon=icon, version=version_info("opensweep-cli.exe"), upx=False)

coll = COLLECT(gui_exe, gui_a.binaries, gui_a.datas, cli_exe, cli_a.binaries, cli_a.datas,
               name="OpenSweep", upx=False)
