"""Stage 3: low-poly OBJ -> printable papercraft template via Blender headless."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.config import BLENDER_SCRIPTS_DIR, settings

log = logging.getLogger(__name__)


def _resolve_blender() -> str:
    """Find the Blender executable, in this order:

    1. ``BLENDER_PATH`` env var / .env
    2. ``blender`` on PATH
    3. Common Windows install paths
    """
    if settings.BLENDER_PATH and Path(settings.BLENDER_PATH).exists():
        return settings.BLENDER_PATH

    on_path = shutil.which("blender")
    if on_path:
        return on_path

    common = [
        r"C:\Program Files\Blender Foundation\Blender 4.4\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/usr/bin/blender",
        "/usr/local/bin/blender",
    ]
    for p in common:
        if Path(p).exists():
            return p

    raise RuntimeError(
        "Could not locate Blender. Set BLENDER_PATH in .env to the full "
        "path of blender.exe (e.g. "
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe)"
    )


def unfold(obj_path: Path, output_pdf: Path) -> Path:
    """Run Blender in headless mode to unfold ``obj_path`` to ``output_pdf``."""
    blender = _resolve_blender()
    script = BLENDER_SCRIPTS_DIR / "unfold.py"

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        blender,
        "--background",
        "--factory-startup",
        "--python", str(script),
        "--",
        str(obj_path),
        str(output_pdf),
        settings.PAGE_SIZE,
    ]

    log.info("Running Blender: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if proc.stdout:
        log.info("Blender stdout:\n%s", proc.stdout)
    if proc.stderr:
        log.info("Blender stderr:\n%s", proc.stderr)

    if proc.returncode != 0 or not output_pdf.exists():
        raise RuntimeError(
            f"Blender unfold failed (rc={proc.returncode}). "
            f"Tail: {proc.stdout[-1500:] if proc.stdout else ''} "
            f"{proc.stderr[-500:] if proc.stderr else ''}"
        )
    return output_pdf
