import json
from pathlib import Path

import pytest

from opensweep.core import engine, providers
from opensweep.core.platform import platform_key
from opensweep.core.rules import Rule


def _rule(path: Path, **kw) -> Rule:
    return Rule(id=kw.pop("id", "test"), category=kw.pop("category", "dev"), name={"en": "Test"},
                paths={platform_key(): [str(path)]}, **kw)


def _file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


@pytest.fixture(autouse=True)
def _no_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "log_dir", lambda: tmp_path)


def test_scan_and_clean_contents_only(tmp_path):
    root = tmp_path / "cache"
    _file(root / "a" / "1.bin", 10)
    _file(root / "2.bin", 5)
    res = engine.scan_rule(_rule(root), running=set(), admin=False)
    assert res.size == 15 and len(res.targets) == 1 and res.targets[0].keep_root
    report = engine.clean(res.targets)
    assert report.freed == 15
    assert root.is_dir() and not any(root.iterdir())


def test_itemized_huggingface_layout(tmp_path):
    hub = tmp_path / "hub"
    _file(hub / "models--meta-llama--Llama-3-8B" / "blobs" / "x", 30)
    _file(hub / "datasets--squad" / "blobs" / "y", 20)
    _file(hub / ".locks" / "z", 1)
    rule = _rule(hub, risk="review", itemize=1, item_patterns=["models--*", "datasets--*"],
                 label_style="huggingface")
    res = engine.scan_rule(rule, running=set(), admin=False)
    labels = [t.label for t in res.targets]
    assert labels == ["meta-llama/Llama-3-8B  [model]", "squad  [dataset]"]
    report = engine.clean([res.targets[1]], use_recycle_bin=False)
    assert report.freed == 20
    assert not (hub / "datasets--squad").exists()
    assert (hub / "models--meta-llama--Llama-3-8B").exists()
    assert (hub / ".locks").exists()


def test_running_app_blocks_rule(tmp_path):
    _file(tmp_path / "c" / "f", 1)
    res = engine.scan_rule(_rule(tmp_path / "c", close_apps=["Code.exe"]), running={"code.exe"}, admin=True)
    assert res.blocked == "running" and not res.cleanable


def test_admin_rule_blocked_without_admin(tmp_path):
    _file(tmp_path / "c" / "f", 1)
    res = engine.scan_rule(_rule(tmp_path / "c", requires_admin=True), running=set(), admin=False)
    assert res.blocked == "admin"


def test_report_rules_are_never_cleaned(tmp_path):
    _file(tmp_path / "disk.vhdx", 9)
    res = engine.scan_rule(_rule(tmp_path / "disk.vhdx", action="report", contents_only=False),
                           running=set(), admin=True)
    assert res.size == 9
    engine.clean(res.targets)
    assert (tmp_path / "disk.vhdx").exists()


def test_protected_root_is_never_deleted_whole(tmp_path):
    rule = _rule(Path.home(), contents_only=False)
    res = engine.scan_rule(rule, running=set(), admin=True)
    assert res.targets == []


# ------------------------------------------------------------------ ollama

def _ollama_model(models: Path, name: str, tag: str, blobs: dict[str, int]) -> None:
    manifest = models / "manifests" / "registry.ollama.ai" / "library" / name / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    layers = [{"digest": d, "size": s} for d, s in blobs.items()]
    manifest.write_text(json.dumps({"layers": layers[1:], "config": layers[0]}))
    for digest, size in blobs.items():
        _file(models / "blobs" / digest.replace(":", "-"), size)


def test_ollama_remove_keeps_shared_blobs(tmp_path, monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda _: None)
    models = tmp_path / "models"
    _ollama_model(models, "llama3", "latest", {"sha256:cfg1": 1, "sha256:shared": 100, "sha256:only1": 50})
    _ollama_model(models, "llama3", "instruct", {"sha256:cfg2": 1, "sha256:shared": 100})
    found = {m.name: m for m in providers.list_ollama_models(models)}
    assert set(found) == {"llama3:latest", "llama3:instruct"}
    assert found["llama3:latest"].size == 151

    res = providers.remove_ollama_model("llama3:latest", models)
    assert res.deleted == 1 and res.freed == 51
    assert (models / "blobs" / "sha256-shared").exists()
    assert not (models / "blobs" / "sha256-only1").exists()
    assert [m.name for m in providers.list_ollama_models(models)] == ["llama3:instruct"]
