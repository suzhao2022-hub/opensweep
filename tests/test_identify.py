"""Plain-language hints for folders the disk map has no fixed explanation for."""
import os

import pytest

from opensweep.core import diskmap, identify, software
from opensweep.i18n import available, t, set_language


@pytest.fixture
def programs(monkeypatch, tmp_path):
    local = tmp_path / "Local"
    files = tmp_path / "Program Files"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("ProgramFiles", str(files))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files (x86)"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "me"))
    fake = [
        software.Program("k1", "HKLM", "Spotify 1.2.3", publisher="Spotify AB",
                         install_location=str(local / "Spotify")),
        software.Program("k2", "HKLM", "Blender", publisher="Blender Foundation",
                         install_location=str(tmp_path / "Tools" / "Blender 4")),
    ]
    monkeypatch.setattr(software, "list_programs", lambda with_usage=True: fake)
    monkeypatch.setattr(identify, "IS_WINDOWS", True)
    identify.reset()
    yield tmp_path
    identify.reset()


def test_folder_inside_an_installed_program(programs):
    found = identify.identify(str(programs / "Tools" / "Blender 4" / "2.0" / "scripts"), True)
    assert (found.kind, found.name, found.publisher) == ("program", "Blender", "Blender Foundation")


def test_appdata_folder_is_matched_to_a_program_by_name(programs):
    found = identify.identify(str(programs / "Roaming" / "Blender" / "cache"), True)
    assert found.kind == "data_of" and found.name == "Blender"
    inside_files = identify.identify(str(programs / "Program Files" / "Blender"), True)
    assert inside_files.kind == "program"  # same name under Program Files means the program itself


def test_generic_and_short_names_are_not_guessed(programs):
    assert identify.identify(str(programs / "Local" / "Cache"), True) is None
    assert identify.identify(str(programs / "Local" / "ab"), True) is None
    assert identify.identify(str(programs / "Local" / "Microsoft"), True) is None


def test_version_numbers_are_not_part_of_the_name(programs):
    found = identify.identify(str(programs / "Local" / "Spotify" / "Data"), True)
    assert found.name == "Spotify" and found.who == "Spotify · Spotify AB"


def test_folder_with_a_program_inside_is_described_by_its_version_info(programs, monkeypatch):
    folder = programs / "Stuff" / "tool"
    folder.mkdir(parents=True)
    (folder / "tool.exe").write_bytes(b"MZ")
    monkeypatch.setattr(software, "exe_description", lambda p: "Disk Doctor")
    monkeypatch.setattr(software, "exe_company", lambda p: "Acme Ltd")
    found = identify.identify(str(folder.parent), True)
    assert (found.kind, found.name, found.publisher) == ("exe_folder", "Disk Doctor", "Acme Ltd")
    monkeypatch.setattr(software, "exe_company", lambda p: "Microsoft Corporation")
    assert identify.identify(str(folder.parent), True).kind == "windows"


def test_program_files_get_their_version_description(programs, monkeypatch):
    dll = programs / "x" / "thing.dll"
    dll.parent.mkdir()
    dll.write_bytes(b"MZ")
    monkeypatch.setattr(software, "exe_description", lambda p: "Audio helper")
    monkeypatch.setattr(software, "exe_company", lambda p: "Acme")
    assert identify.identify(str(dll), False).kind == "file"
    text = programs / "x" / "notes.txt"
    text.write_text("hi")
    assert identify.identify(str(text), False) is None


def test_type_mix_names_what_a_folder_holds():
    root = diskmap.Node("d", r"C:\d")
    for name, size in (("a.mp4", 700), ("b.jpg", 200), ("c.txt", 5), ("d.zip", 95)):
        root.children.append(diskmap.Node(name, "C:\\d\\" + name, size=size, is_dir=False, parent=root))
    mix = dict(diskmap.type_mix(root))
    assert mix == {"video": 0.7, "image": 0.2}  # the zip (9.5%) and the text file are too small to mention
    assert diskmap.type_mix(diskmap.Node("empty", r"C:\empty")) == []


def test_new_well_known_folders_are_explained():
    root = diskmap.Node("D:", "D:\\")
    project = diskmap.Node("app", "D:\\app", parent=root)
    modules = diskmap.Node("node_modules", "D:\\app\\node_modules", parent=project)
    deep = diskmap.Node("react", "D:\\app\\node_modules\\react", parent=modules)
    assert diskmap.explain(modules).id == "node_modules"
    assert diskmap.explain(deep).id == "in:node_modules"
    assert diskmap.explain(diskmap.Node(".git", "D:\\app\\.git", parent=project)).id == "git"


@pytest.mark.parametrize("language", list(available()))
def test_every_language_has_the_new_explanations(language):
    set_language(language)
    for key in ("k_package_cache", "k_node_modules", "k_git", "k_pycache", "k_venv", "k_driver_extract",
                "id_program", "id_data_of", "id_exe_folder", "id_windows", "id_file", "id_contents"):
        assert t("disk." + key) != "disk." + key, (language, key)
    assert "{who}" not in t("disk.id_program", who="X") and "X" in t("disk.id_program", who="X")
    assert "{types}" not in t("disk.id_contents", types="Y")
    set_language("en")


def test_disk_page_adds_what_windows_knows_to_the_explanation(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from opensweep.gui.pages import disk
    QApplication.instance() or QApplication([])
    root = diskmap.Node("D:", "D:\\")
    folder = diskmap.Node("Mystery", r"D:\Mystery", parent=root)
    root.children.append(folder)
    page = disk.DiskPage()
    page.map, page.current = None, root
    shown = []
    page.map_detail.present = lambda icon, title, kind, badges, size, text, *a, **k: shown.append((badges, text))
    page._ident[folder.path] = identify.Identity("program", "Blender", "Blender Foundation")
    page._present(page.map_detail, folder)
    badges, text = shown[-1]
    assert "Blender · Blender Foundation" in text and "uninstall" in text.lower()
    assert badges[0][0] == "Don't delete by hand"
    assert not page._deletable(folder, None, page._ident[folder.path])
    page.close()
