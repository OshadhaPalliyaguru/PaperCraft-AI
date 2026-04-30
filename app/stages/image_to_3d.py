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

    Two endpoints are available:
      * ``/shape_generation`` — geometry only, ~30 GPU seconds. CHEAP.
      * ``/generation_all``   — geometry + textures, ~90 GPU seconds. PRETTY but uses 3x your daily ZeroGPU quota.

    Picks based on ``settings.WITH_TEXTURE``.
    """
    if settings.WITH_TEXTURE:
        api_name = "/generation_all"
    else:
        api_name = "/shape_generation"

    log.info("Hunyuan3D endpoint: %s (WITH_TEXTURE=%s)", api_name, settings.WITH_TEXTURE)

    result = client.predict(
        None,                          # caption
        handle_file(str(image_path)),  # image
        None, None, None, None,        # multi-view images (front/back/left/right)
        30,                            # steps
        5.0,                           # guidance_scale
        1234,                          # seed
        256,                           # octree_resolution
        True,                          # check_box_rembg
        8000,                          # num_chunks
        True,                          # randomize_seed
        api_name=api_name,
    )

    # The /generation_all returns (shape_glb, textured_glb, ...). We want the textured one when present.
    if isinstance(result, (list, tuple)):
        mesh_paths: list[Path] = []
        mesh_exts = (".glb", ".obj", ".ply", ".gltf")
        for x in result:
            if isinstance(x, str) and x.lower().endswith(mesh_exts) and Path(x).exists():
                mesh_paths.append(Path(x))
        if mesh_paths:
            chosen = mesh_paths[-1] if len(mesh_paths) > 1 else mesh_paths[0]
            if len(mesh_paths) > 1 and chosen.stat().st_size < mesh_paths[0].stat().st_size * 0.5:
                chosen = mesh_paths[0]
            log.info("Hunyuan3D returned %d files; chose %s (%d bytes)",
                     len(mesh_paths), chosen.name, chosen.stat().st_size)
            return chosen

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


def _is_quota_error(e: Exception) -> bool:
    """True if the exception looks like an HF ZeroGPU quota / rate-limit error."""
    msg = str(e).lower()
    return any(
        kw in msg
        for kw in (
            "zerogpu quota",
            "running out of",
            "rate limit",
            "too many requests",
            "exceeded your free",
            "subscribe/pro",
        )
    )


def generate_mesh(
    image_path: Path,
    job_id: str,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Run image -> 3D and return the path to a GLB/OBJ in the job's tmp dir.

    Tries every (token, space) pair until one succeeds. Tokens are
    rotated on quota errors so multiple free HF accounts stack their
    daily ZeroGPU minutes.
    """
    job_tmp = TMP_DIR / job_id
    job_tmp.mkdir(parents=True, exist_ok=True)

    primary = settings.HF_SPACE_ID
    space_order = [primary] + [s for s in FALLBACK_ORDER if s != primary]
    tokens = settings.hf_tokens() or [None]  # at least one None = anonymous

    tried: list[str] = []
    last_err: Exception | None = None

    for token_idx, token in enumerate(tokens):
        token_label = f"token#{token_idx + 1}" if token else "anonymous"
        for space_id in space_order:
            tried.append(f"{space_id} ({token_label})")
            if on_status:
                on_status(f"Calling {space_id} via {token_label}...")
            log.info("Trying Space %s with %s", space_id, token_label)
            try:
                kwargs = {"hf_token": token} if token else {}
                client = Client(space_id, **kwargs)
                adapter = ADAPTERS.get(space_id, _adapt_generic)
                result_path = adapter(client, image_path)
                dest = job_tmp / f"mesh{result_path.suffix.lower()}"
                shutil.copy2(result_path, dest)
                log.info("Got mesh from %s (%s) -> %s", space_id, token_label, dest)
                return dest
            except Exception as e:
                log.warning(
                    "Space %s via %s failed: %s",
                    space_id,
                    token_label,
                    e,
                )
                if log.isEnabledFor(logging.DEBUG):
                    log.debug("Trace: %s", traceback.format_exc())
                last_err = e
                # If THIS Space says quota exhausted, rotate to the next
                # token immediately rather than trying more Spaces with
                # the same token (they share the per-user pool).
                if _is_quota_error(e):
                    log.info("Quota error detected; rotating to next token if available.")
                    break
                continue

    hint = ""
    if last_err and _is_quota_error(last_err):
        hint = (
            "\n\nYour Hugging Face daily ZeroGPU quota is exhausted. Options:\n"
            "  - Set WITH_TEXTURE=False in .env (uses ~3x less GPU per call).\n"
            "  - Add more free HF accounts: HF_TOKEN=hf_xxx,hf_yyy,hf_zzz in .env.\n"
            "  - HF Pro ($9/mo) for 25 min/day.\n"
            "  - Self-host on free Google Colab T4 GPU (effectively unlimited)."
        )

    raise RuntimeError(
        "All image-to-3D attempts failed. Tried: "
        + ", ".join(tried)
        + f". Last error ({type(last_err).__name__}): {last_err}"
        + hint
    )
