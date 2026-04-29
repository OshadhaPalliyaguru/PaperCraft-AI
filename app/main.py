"""FastAPI entrypoint for the papercraft-ai web app."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import OUTPUTS_DIR, STATIC_DIR, TMP_DIR
from app.jobs import store
from app.pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

app = FastAPI(title="Papercraft AI", version="0.1.0")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/jobs")
async def create_job(background_tasks: BackgroundTasks, image: UploadFile = File(...)):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(400, "Please upload an image file")

    suffix = Path(image.filename or "input.png").suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"

    tmp_image = TMP_DIR / f"upload_{Path(image.filename or 'x').stem}{suffix}"
    with tmp_image.open("wb") as f:
        shutil.copyfileobj(image.file, f)

    job = store.create(image_path=tmp_image)

    final_image = TMP_DIR / job.id / f"input{suffix}"
    final_image.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_image), str(final_image))
    store.update(job.id, image_path=final_image)

    background_tasks.add_task(run_pipeline, job.id)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/template.pdf")
def download_pdf(job_id: str):
    job = store.get(job_id)
    if not job or not job.pdf_path or not job.pdf_path.exists():
        raise HTTPException(404, "Template not ready")
    return FileResponse(
        path=str(job.pdf_path),
        media_type="application/pdf",
        filename=f"papercraft-{job_id}.pdf",
    )


@app.get("/api/jobs/{job_id}/model.glb")
def download_glb(job_id: str):
    job = store.get(job_id)
    if not job or not job.glb_path or not job.glb_path.exists():
        raise HTTPException(404, "Model not ready")
    return FileResponse(
        path=str(job.glb_path),
        media_type="model/gltf-binary",
        filename=f"papercraft-{job_id}.glb",
    )


# Serve the static UI last so /api routes take priority.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
