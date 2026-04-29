"""Stage 1: Image -> 3D mesh via a free Hugging Face Space.

We use ``gradio_client`` to call public Spaces. APIs differ per Space,
so we keep a small registry of adapters. To add support for a new
Space, write an adapter function and register it.

If a Space is overloaded / changes its API / requires login, swap to
another preset by editing ``HF_SPACE_ID`` in ``.env``.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from gradio_client import Client, handle_file

from app.config import TMP_DIR, settings

log = logging.getLogger(__name__)


# Each adapter takes (Client, image_path) and returns a path to a GLB/OBJ file.
Adapter = Callable[[Client, Path], Path]


def _adapt_triposr(client: Client, image_path: Path) -> Path:
    """Adapter for stabilityai/TripoSR."""
    # TripoSR pipeline: preprocess -> generate
    try:
        prepped = client.predict(
            handle_file(str(image_path)),
            True,  # do_remove_background
            0.85,  # foreground_ratio
            api_name="/preprocess",
        )
    except Exception:
        prepped = image_path
    result = client.predict(
        handle_file(str(prepped)) if isinstance(prepped, str) else prepped,
        256,  # mc_resolution
        ["obj", "glb"],
        api_name="/generate",
    )
    return _first_mesh_path(result)


def _adapt_hunyuan3d(client: Client, image_path: Path) -> Path:
    """Adapter for tencent/Hunyuan3D-2 family Spaces."""
    result = client.predict(
        handle_file(str(image_path)),
        api_name="/generate_shape_only",
    )
    return _first_mesh_path(result)


def _adapt_trellis(client: Client, image_path: Path) -> Path:
    """Adapter for JeffreyXiang/TRELLIS."""
    result = client.predict(
        handle_file(str(image_path)),
        "Random",  # seed
        api_name="/image_to_3d",
    )
    return _first_mesh_path(result)


def _adapt_generic(client: Client, image_path: Path) -> Path:
    """Last-ditch: try the first endpoint and grab anything mesh-like."""
    endpoints = client.view_api(return_format="dict") or {}
    log.info("Generic adapter inspected endpoints: %s", list(endpoints))
    api_name = next(iter(endpoints), None) or "/predict"
    result = client.predict(handle_file(str(image_path)), api_name=api_name)
    return _first_mesh_path(result)


ADAPTERS: dict[str, Adapter] = {
    "stabilityai/TripoSR": _adapt_triposr,
    "tencent/Hunyuan3D-2": _adapt_hunyuan3d,
    "tencent/Hunyuan3D-2mini": _adapt_hunyuan3d,
    "JeffreyXiang/TRELLIS": _adapt_trellis,
}

# Tried in order if the configured Space fails.
FALLBACK_ORDER = [
    "stabilityai/TripoSR",
    "tencent/Hunyuan3D-2mini",
    "JeffreyXiang/TRELLIS",
]


def _first_mesh_path(result) -> Path:
    """Walk a Gradio result (str / list / tuple / dict) and find a mesh file."""
    if result is None:
        raise RuntimeError("Space returned no result")

    candidates: list[str] = []

    def walk(x):
        if isinstance(x, str):
            candidates.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(result)
    mesh_exts = (".glb", ".obj", ".ply", ".gltf", ".stl")
    for c in candidates:
        if c.lower().endswith(mesh_exts) and Path(c).exists():
            return Path(c)
    raise RuntimeError(f"No mesh file in Space output: {candidates}")


def generate_mesh(
    image_path: Path,
    job_id: str,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Run image -> 3D and return the path to a GLB/OBJ in the job's tmp dir.

    Tries the configured Space first, then falls back through ``FALLBACK_ORDER``.
    """
    job_tmp = TMP_DIR / job_id
    job_tmp.mkdir(parents=True, exist_ok=True)

    primary = settings.HF_SPACE_ID
    tried = []
    last_err: Exception | None = None

    space_order = [primary] + [s for s in FALLBACK_ORDER if s != primary]

    for space_id in space_order:
        tried.append(space_id)
        if on_status:
            on_status(f"Calling Hugging Face Space: {space_id}")
        log.info("Trying Space %s", space_id)
        try:
            kwargs = {"hf_token": settings.HF_TOKEN} if settings.HF_TOKEN else {}
            client = Client(space_id, **kwargs)
            adapter = ADAPTERS.get(space_id, _adapt_generic)
            result_path = adapter(client, image_path)
            dest = job_tmp / f"mesh{result_path.suffix.lower()}"
            shutil.copy2(result_path, dest)
            log.info("Got mesh from %s -> %s", space_id, dest)
            return dest
        except Exception as e:
            log.warning("Space %s failed: %s", space_id, e)
            last_err = e
            continue

    raise RuntimeError(
        "All image-to-3D Spaces failed. Tried: "
        + ", ".join(tried)
        + f". Last error: {last_err}"
    )
