"""End-to-end pipeline: image -> 3D -> low-poly -> papercraft template."""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

from app.config import OUTPUTS_DIR, TMP_DIR
from app.jobs import JobStatus, store
from app.stages import decimate, image_to_3d, unfold

log = logging.getLogger(__name__)


def run_pipeline(job_id: str) -> None:
    """Run the full pipeline for a job. Designed to be called from a
    background thread / task. Updates the job store as it progresses."""
    job = store.get(job_id)
    if not job or not job.image_path:
        log.error("Pipeline called for missing job %s", job_id)
        return

    job_tmp = TMP_DIR / job_id
    job_tmp.mkdir(parents=True, exist_ok=True)
    job_out = OUTPUTS_DIR / job_id
    job_out.mkdir(parents=True, exist_ok=True)

    try:
        store.update(
            job_id,
            status=JobStatus.GENERATING_3D,
            message="Generating 3D model from image...",
            progress=10,
        )
        mesh_path = image_to_3d.generate_mesh(
            image_path=job.image_path,
            job_id=job_id,
            on_status=lambda s: store.update(job_id, message=s),
        )
        store.update(job_id, glb_path=mesh_path, progress=55)

        store.update(
            job_id,
            status=JobStatus.DECIMATING,
            message="Simplifying mesh to low-poly...",
            progress=60,
        )
        obj_path = job_tmp / "lowpoly.obj"
        decimate.decimate(mesh_path, obj_path)
        store.update(job_id, obj_path=obj_path, progress=70)

        store.update(
            job_id,
            status=JobStatus.UNFOLDING,
            message="Unfolding into a printable template...",
            progress=75,
        )
        pdf_path = job_out / "template.pdf"
        unfold.unfold(obj_path, pdf_path)
        store.update(
            job_id,
            status=JobStatus.DONE,
            message="Template ready!",
            progress=100,
            pdf_path=pdf_path,
        )
        log.info("Job %s done -> %s", job_id, pdf_path)

    except Exception as e:
        tb = traceback.format_exc()
        log.exception("Job %s failed", job_id)
        store.update(
            job_id,
            status=JobStatus.FAILED,
            error=f"{e}",
            message="Pipeline failed",
            progress=100,
        )
        # Keep the trace on disk for debugging
        (job_tmp / "error.log").write_text(tb, encoding="utf-8")
