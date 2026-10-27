"""Special cleaners that need more than deleting folders.

- recycle_bin: asks Windows for the Recycle Bin size and empties it.
- ollama: lists Ollama models from their manifests and removes them without
  breaking blobs that other models still share.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from opensweep.core.fsutil import DeleteResult
from opensweep.core.platform import IS_WINDOWS

# ---------------------------------------------------------------- Recycle Bin


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]


def recycle_bin_stats() -> tuple[int, int]:
    """(bytes, item count) across all drives. (0, 0) when unavailable."""
    if not IS_WINDOWS:
        return 0, 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> DeleteResult:
    size, count = recycle_bin_stats()
    if not IS_WINDOWS or count == 0:
        return DeleteResult()
    flags = 0x1 | 0x2 | 0x4  # no confirmation, no progress UI, no sound
    hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    if hr != 0:
        return DeleteResult(failed=count)
    return DeleteResult(freed=size, deleted=count)


# ------------------------------------------------------- Chromium / Electron apps

# Folders every Chromium-based app (Electron, CEF, Chrome-like browsers) uses for caches only.
CHROMIUM_CACHES = ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
                   "GrShaderCache", "ShaderCache", "GraphiteDawnCache", os.path.join("Service Worker", "CacheStorage"),
                   os.path.join("Service Worker", "ScriptCache"))
# A folder is a Chromium profile if it has one of these next to its caches.
CHROMIUM_MARKERS = ("Local State", "Preferences", "Local Storage", "Network", "Session Storage")
# Company folders that hold several apps: label apps by the folder below them.
VENDOR_FOLDERS = {"tencent", "netease", "google", "nvidia corporation", "baidu", "alibaba", "bytedance", "kingsoft",
                  "flutter_webview_windows", "adobe", "programs", "mozilla"}
_SKIP_DIRS = {"packages", "temp", "microsoft", "node_modules", "pip", "npm-cache", "uv", "go-build", "nvidia",
              "d3dscache", "crashdumps", "programs", "docker", "jetbrains", "ms-playwright", ".git"}


@dataclass
class ChromiumApp:
    label: str
    root: str
    caches: list[str]


def _is_profile(names: set[str]) -> bool:
    return any(m.lower() in names for m in CHROMIUM_MARKERS)


def find_chromium_apps(bases: list[str] | None = None, max_depth: int = 4) -> list[ChromiumApp]:
    """Every app folder under AppData that contains Chromium cache folders."""
    if bases is None:
        bases = [os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")]
    apps: dict[str, ChromiumApp] = {}
    for base in filter(None, bases):
        stack = [(base, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                entries = [e for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            names = {e.name.lower() for e in entries}
            try:
                names |= {n.lower() for n in os.listdir(folder)}
            except OSError:
                pass
            if depth > 0 and _is_profile(names):
                caches = [os.path.join(folder, c) for c in CHROMIUM_CACHES if os.path.isdir(os.path.join(folder, c))]
                if caches:
                    # Group by the app's own folder: "Tencent\QQ\...\Cache" -> "Tencent\QQ", "douyin\..." -> "douyin"
                    rel = os.path.relpath(folder, base).split(os.sep)
                    keep = 2 if rel[0].lower() in VENDOR_FOLDERS and len(rel) > 1 else 1
                    root = os.path.join(base, *rel[:keep])
                    app = apps.setdefault(root.lower(), ChromiumApp(rel[keep - 1], root, []))
                    app.caches.extend(caches)
                    continue  # don't look for profiles inside a profile
            if depth >= max_depth:
                continue
            for e in entries:
                if e.name.lower() in _SKIP_DIRS or e.name.startswith("."):
                    continue
                if e.name in CHROMIUM_CACHES or e.name.lower() in ("cache", "code cache", "gpucache"):
                    continue
                stack.append((e.path, depth + 1))
    return sorted(apps.values(), key=lambda a: a.label.lower())


# --------------------------------------------------------------------- Ollama


@dataclass
class OllamaModel:
    name: str
    manifest: Path
    blobs: list[str]
    size: int
    modified: float


def ollama_models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


def _blob_file(models_dir: Path, digest: str) -> Path:
    return models_dir / "blobs" / digest.replace(":", "-")


def _model_name(manifests: Path, manifest: Path) -> str:
    parts = manifest.relative_to(manifests).parts  # registry/namespace/model/tag
    if len(parts) < 4:
        return "/".join(parts)
    registry, namespace, model, tag = parts[0], parts[1], "/".join(parts[2:-1]), parts[-1]
    if registry == "registry.ollama.ai":
        prefix = "" if namespace == "library" else f"{namespace}/"
    else:
        prefix = f"{registry}/{namespace}/"
    return f"{prefix}{model}:{tag}"


def list_ollama_models(models_dir: Path | None = None) -> list[OllamaModel]:
    models_dir = models_dir or ollama_models_dir()
    manifests = models_dir / "manifests"
    if not manifests.is_dir():
        return []
    models = []
    for manifest in manifests.rglob("*"):
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        layers = list(data.get("layers", []))
        if isinstance(data.get("config"), dict):
            layers.append(data["config"])
        blobs = [layer["digest"] for layer in layers if "digest" in layer]
        size = 0
        for digest in blobs:
            try:
                size += _blob_file(models_dir, digest).stat().st_size
            except OSError:
                pass
        models.append(OllamaModel(_model_name(manifests, manifest), manifest, blobs, size,
                                  manifest.stat().st_mtime))
    return sorted(models, key=lambda m: m.size, reverse=True)


def remove_ollama_model(name: str, models_dir: Path | None = None) -> DeleteResult:
    """Prefer `ollama rm`; fall back to removing the manifest and unshared blobs."""
    models_dir = models_dir or ollama_models_dir()
    before = {m.name: m for m in list_ollama_models(models_dir)}
    model = before.get(name)
    if model is None:
        return DeleteResult()
    sizes = {}
    for digest in model.blobs:
        try:
            sizes[digest] = _blob_file(models_dir, digest).stat().st_size
        except OSError:
            sizes[digest] = 0
    exe = shutil.which("ollama")
    if exe:
        try:
            flags = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW
            subprocess.run([exe, "rm", name], capture_output=True, timeout=60, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            pass
    if model.manifest.exists():
        _remove_manually(model, models_dir)
    if name in {m.name for m in list_ollama_models(models_dir)}:
        return DeleteResult(failed=1)
    freed = sum(size for digest, size in sizes.items() if not _blob_file(models_dir, digest).exists())
    return DeleteResult(freed=freed, deleted=1)


def _remove_manually(model: OllamaModel, models_dir: Path) -> None:
    others = {d for m in list_ollama_models(models_dir) if m.name != model.name for d in m.blobs}
    try:
        model.manifest.unlink()
    except OSError:
        return
    for digest in model.blobs:
        if digest in others:
            continue
        blob = _blob_file(models_dir, digest)
        try:
            blob.unlink()
        except OSError:
            pass
