import json
import os
from pathlib import Path

from opensweep.core import diskmap
from opensweep.core.platform import resource_path

MB = 1024 ** 2


def _file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)  # sparse on most file systems: fast even for big sizes


def test_scan_sizes_and_largest(tmp_path):
    _file(tmp_path / "videos" / "a.mp4", 30 * MB)
    _file(tmp_path / "videos" / "old" / "b.mkv", 20 * MB)
    _file(tmp_path / "small" / "x.txt", 100)
    _file(tmp_path / "small" / "y.txt", 200)
    _file(tmp_path / "top.iso", 5 * MB)
    m = diskmap.scan(str(tmp_path), workers=2)
    root = m.root
    assert root.size == 55 * MB + 300 and root.files == 5 and m.files == 5
    names = [c.name for c in root.children]
    assert names[0] == "videos" and root.children[0].size == 50 * MB
    small = next(c for c in root.children if c.name == "small")
    assert small.children[0].kind == "small" and small.children[0].files == 2
    assert [f.name for f in m.largest[:3]] == ["a.mp4", "b.mkv", "top.iso"]
    assert m.types["video"] == [50 * MB, 2] and m.types["diskimage"] == [5 * MB, 1]
    videos = root.children[0]
    assert diskmap.path_to(videos.children[0])[0] is root


def test_remove_node_updates_parents(tmp_path):
    _file(tmp_path / "d" / "big.bin", 8 * MB)
    _file(tmp_path / "d" / "other.bin", 2 * MB)
    m = diskmap.scan(str(tmp_path), workers=1)
    d = m.root.children[0]
    big = d.children[0]
    diskmap.remove_node(big)
    assert d.size == 2 * MB and m.root.size == 2 * MB and big not in d.children


def test_explain_known_paths_and_types(tmp_path, monkeypatch):
    home = Path.home()
    root = diskmap.Node("C:", "C:\\")
    users = diskmap.Node("Downloads", str(home / "Downloads"), parent=root)
    video = diskmap.Node("movie.mp4", str(home / "Downloads" / "movie.mp4"), is_dir=False, parent=users)
    known = diskmap.explain(users)
    assert known.id == "downloads" and known.safety == "user"
    assert diskmap.explain(video).id == "type:video"
    temp_dir = os.environ.get("TEMP", str(tmp_path))
    temp = diskmap.Node("Temp", temp_dir, parent=root)
    inside = diskmap.Node("x", os.path.join(temp_dir, "x"), parent=temp)
    assert diskmap.explain(temp).id == "temp"
    assert diskmap.explain(inside).id == "in:temp" and diskmap.explain(inside).safety == "clean"
    assert diskmap.explain(diskmap.Node("", "C:\\", kind="small")).id == "small"


def test_squarify_fills_the_rectangle():
    from PySide6.QtCore import QRectF
    from opensweep.gui.treemap import squarify
    rect = QRectF(0, 0, 400, 300)
    sizes = [50, 25, 12, 8, 3, 2]
    rects = squarify(sizes, rect)
    area = sum(r.width() * r.height() for r in rects)
    assert abs(area - 400 * 300) < 1
    for r, s in zip(rects, sizes):
        assert abs(r.width() * r.height() - 400 * 300 * s / sum(sizes)) < 1
        assert rect.adjusted(-0.01, -0.01, 0.01, 0.01).contains(r)


def _en():
    return json.loads(resource_path("locales", "en.json").read_text(encoding="utf-8"))


def test_every_known_folder_and_startup_kind_has_text():
    en = _en()
    known = json.loads(resource_path("data", "known_paths.json").read_text(encoding="utf-8"))
    for entry in known["paths"]:
        assert f"disk.k_{entry['id']}" in en, entry["id"]
    for kind in diskmap.TYPES:
        assert f"disk.t_{kind}" in en and f"disk.type_{kind}" in en, kind
    advice = json.loads(resource_path("data", "startup_advice.json").read_text(encoding="utf-8"))
    for kind in advice["advice"]:
        assert f"startup.why_{kind}" in en and f"startup.kind_{kind}" in en, kind
