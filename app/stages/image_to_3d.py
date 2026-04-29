"""Stage 1: Image -> 3D mesh via a free Hugging Face Space.

Adapters were verified against the live Space APIs via ``probe_spaces.py``.
If a Space changes its API again, re-run::

    .\\.venv\\Scripts\\python.exe probe_spaces.py

and update the adapter to match the printed signature.
"""
from __future__ import annotations

import logging
import shutil
import traceback
from pathlib import Path
from typing import Callable

from gradio_client import Client, handle_file

from app.config import TMP_DIR, settings

log = logging.getLogger(__name__)


# Each adapter takes (Client, image_path) and returns a path to a GLB/OBJ file.
Adapter = Callable[[Client, Path], Path]


def _adapt_hunyuan3d_2(client: Client, image_path: Path) -> Path:
    """Adapter for ``tencent/Hunyuan3D-2``.

    Endpoint ``/shape_generation`` returns ``(file, output, mesh_stats, seed)``
    where ``file`` is a GLB. We use shape-only (no texture) for speed.
    """
    result = client.predict(
        None,                          # caption
        handle_file(str(image_path)),  # image
        None, None, None, None,        # multi-view images (front/back/left/right)
        30,                            # steps
        5.0,                           # guidance_scale
        1234,                          # seed
        256,                           # octree_resolution
        True,                          # check_box_rembg (background removal)
        8000,                          # num_chunks
        True,                          # randomize_seed
        api_name="/shape_generation",
    )
    return _first_mesh_path(result)


def _adapt_trellis(client: Client, image_path: Path) -> Path:
    """Adapter for ``JeffreyXiang/TRELLIS``.

    TRELLIS is stateful and needs a 3-step flow:
      1. ``/start_session`` (server-side state)
      2. ``/preprocess_image`` -> cleaned image
      3. ``/image_to_3d``      -> generates internal Gaussian, returns a preview video
      4. ``/extract_glb``      -> finally produces the GLB file
    """
    try:
        client.predict(api_name="/start_session")
    except Exception as e:
        log.debug("TRELLIS start_session non-fatal error: %s", e)

    prepped = client.predict(
        handle_file(str(image_path)),
        api_name="/preprocess_image",
    )
    image_arg = handle_file(prepped) if isinstance(prepped, str) else handle_file(str(image_path))

    client.predict(
        image_arg,
        [],          # multiimages
        0,           # seed
        7.5,         # ss_guidance_strength
        12,          # ss_sampling_steps
        3.0,         # slat_guidance_strength
        12,          # slat_sampling_steps
        "stochastic",
        api_name="/image_to_3d",
    )

    glb_result = client.predict(
        0.95,   # mesh_simplify
        1024,   # texture_size
        api_name="/extract_glb",
    )
    return _first_mesh_path(glb_result)


def _adapt_generic(client: Client, image_path: Path) -> Path:
    """Last-ditch: scan endpoints for one that takes an image and returns a file."""
    api = client.view_api(return_format="dict") or {}
    log.info("Generic adapter inspected endpoints: %s", list(api))
    candidates = []
    for name, info in api.items():
        params = (info or {}).get("parameters") or []
        for p in params:
            if isinstance(p, dict) and (p.get("python_type", {}) or {}).get("type") in {"filepath", "str"}:
                candidates.append(name)
                break
    api_name = candidates[0] if candidates else next(iter(api), "/predict")
    result = client.predict(handle_file(str(image_path)), api_name=api_name)
    return _first_mesh_path(result)


ADAPTERS: dict[str, Adapter] = {
    "tencent/Hunyuan3D-2": _adapt_hunyuan3d_2,
    "JeffreyXiang/TRELLIS": _adapt_trellis,
}

# Tried in order if the configured Space fails. We only list Spaces that
# the probe confirmed are currently up and have a working adapter.
FALLBACK_ORDER = [
    "tencent/Hunyuan3D-2",
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
            log.warning(
                "Space %s failed: %s\n%s",
                space_id,
                e,
                traceback.format_exc(),
            )
            last_err = e
            continue

    raise RuntimeError(
        "All image-to-3D Spaces failed. Tried: "
        + ", ".join(tried)
        + f". Last error ({type(last_err).__name__}): {last_err}"
    )
