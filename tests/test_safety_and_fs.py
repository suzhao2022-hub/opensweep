import os
import sys
import time
from pathlib import Path

import pytest

from opensweep.core import fsutil
from opensweep.core.safety import UnsafePathError, check_deletable, is_protected


def test_protected_paths():
    home = Path.home()
    assert is_protected(home)
    assert is_protected(home / "Documents" / "thesis.docx")
    assert is_protected(home / "Desktop")
    assert is_protected(os.path.abspath(os.sep))
    if sys.platform == "win32":
        assert is_protected("C:\\")
        assert is_protected(os.environ["SystemRoot"])
        assert is_protected(os.path.join(os.environ["SystemRoot"], "System32", "drivers"))
        assert is_protected(os.environ["LOCALAPPDATA"])
        assert is_protected(os.environ["TEMP"])
        assert not is_protected(os.path.join(os.environ["TEMP"], "some-installer-leftover"))
        assert not is_protected(os.path.join(os.environ["LOCALAPPDATA"], "pip", "Cache"))


def test_check_deletable_requires_inside_root(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    check_deletable(root / "a", root)
    with pytest.raises(UnsafePathError):
        check_deletable(tmp_path / "elsewhere", root)
    with pytest.raises(UnsafePathError):
        check_deletable(root / ".." / "elsewhere", root)


def _make(path: Path, size: int, age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


def test_measure_and_delete(tmp_path):
    _make(tmp_path / "c" / "a.bin", 100)
    _make(tmp_path / "c" / "sub" / "b.bin", 50)
    stats = fsutil.measure(str(tmp_path / "c"))
    assert (stats.size, stats.files) == (150, 2)
    res = fsutil.delete_tree(str(tmp_path / "c"), keep_root=True)
    assert (res.freed, res.deleted, res.failed) == (150, 2, 0)
    assert (tmp_path / "c").is_dir() and not any((tmp_path / "c").iterdir())


def test_min_age_keeps_recent_files(tmp_path):
    old = _make(tmp_path / "t" / "old.tmp", 10, age_days=3)
    new = _make(tmp_path / "t" / "new.tmp", 20)
    assert fsutil.measure(str(tmp_path / "t"), min_age_days=1).size == 10
    fsutil.delete_tree(str(tmp_path / "t"), min_age_days=1, keep_root=True)
    assert not old.exists() and new.exists()


def test_inspect_lists_children_and_types(tmp_path):
    _make(tmp_path / "c" / "big" / "a.bin", 300)
    _make(tmp_path / "c" / "big" / "b.log", 100)
    _make(tmp_path / "c" / "small.tmp", 10)
    _make(tmp_path / "c" / "noext", 5)
    info = fsutil.inspect(str(tmp_path / "c"), limit=2)
    assert [(e.name, e.size, e.files, e.is_dir) for e in info.entries] == [("big", 400, 2, True),
                                                                            ("small.tmp", 10, 1, False)]
    assert info.more == 1
    assert info.total.size == 415 and info.total.files == 4
    assert info.types[0] == (".bin", 300, 1)
    assert ("", 5, 1) in info.types


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_links_are_never_followed(tmp_path):
    precious = _make(tmp_path / "precious" / "keep.txt", 5)
    cache = tmp_path / "cache"
    _make(cache / "junk.bin", 7)
    _link_dir(cache / "link", tmp_path / "precious")
    assert fsutil.measure(str(cache)).size == 7  # link target not counted
    fsutil.delete_tree(str(cache), keep_root=True)
    assert precious.exists(), "deleting through a link/junction must never touch its target"
    assert not (cache / "link").exists()


def test_locked_file_is_skipped(tmp_path):
    if sys.platform != "win32":
        pytest.skip("file locking semantics are Windows specific")
    _make(tmp_path / "d" / "free.bin", 3)
    locked = _make(tmp_path / "d" / "locked.bin", 4)
    with open(locked, "rb"):  # an open handle without share-delete blocks deletion on Windows
        res = fsutil.delete_tree(str(tmp_path / "d"), keep_root=True)
    assert res.deleted >= 1
