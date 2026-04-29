"""Stage 2: simplify the AI-generated mesh down to a low-poly version
suitable for papercraft (typically 40-150 faces)."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import trimesh

from app.config import settings

log = logging.getLogger(__name__)


def _as_single_mesh(loaded) -> trimesh.Trimesh:
    """trimesh.load can return Scene; flatten to a single mesh."""
    if isinstance(loaded, trimesh.Scene):
        if len(loaded.geometry) == 0:
            raise RuntimeError("Loaded scene contains no geometry")
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise RuntimeError("Scene has no triangle meshes")
        if len(meshes) == 1:
            return meshes[0]
        return trimesh.util.concatenate(meshes)
    if not isinstance(loaded, trimesh.Trimesh):
        raise RuntimeError(f"Unsupported geometry type: {type(loaded)}")
    return loaded


def _normalize(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Center the mesh and scale so its largest dimension is 1.0.

    Keeps Blender / paper sizes predictable downstream.
    """
    mesh = mesh.copy()
    # Center on origin
    mesh.apply_translation(-mesh.bounding_box.centroid)
    # Scale to unit cube
    extents = mesh.extents
    max_extent = float(max(extents)) if extents.size else 1.0
    if max_extent > 0:
        mesh.apply_scale(1.0 / max_extent)
    return mesh


def decimate(input_mesh_path: Path, output_obj_path: Path) -> Path:
    """Simplify the input mesh and write a clean OBJ.

    We use trimesh's quadric decimation (wraps fast-quadric-mesh-simplification
    when available, falls back to open3d if installed). For mediocre input
    meshes we also clean up duplicate vertices and degenerate faces.
    """
    target = max(20, int(settings.TARGET_FACES))
    log.info("Decimating %s -> %s (target faces=%d)", input_mesh_path, output_obj_path, target)

    loaded = trimesh.load(str(input_mesh_path), force="scene")
    mesh = _as_single_mesh(loaded)

    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()

    if len(mesh.faces) > target:
        simplified = None
        # Try modern API (needs fast-simplification or open3d)
        try:
            simplified = mesh.simplify_quadric_decimation(face_count=target)
        except (ImportError, ModuleNotFoundError, TypeError) as e:
            log.warning("Quadric decimation unavailable (%s); using vertex clustering fallback", e)
        except Exception as e:
            log.warning("Quadric decimation failed (%s); falling back", e)

        # Fallback: trimesh's built-in vertex clustering. No external deps.
        if simplified is None or not isinstance(simplified, trimesh.Trimesh):
            extents = float(max(mesh.extents)) if mesh.extents.size else 1.0
            target_ratio = max(0.02, min(0.5, (target / max(1, len(mesh.faces))) ** 0.5))
            pitch = extents * target_ratio
            try:
                simplified = mesh.simplify_vertex_clustering(pitch=pitch)
            except Exception as e:
                log.warning("Vertex clustering also failed (%s); keeping original mesh", e)
                simplified = mesh

        if isinstance(simplified, trimesh.Trimesh) and len(simplified.faces) > 0:
            mesh = simplified
        log.info("After simplify: %d faces (target=%d)", len(mesh.faces), target)

    mesh = _normalize(mesh)
    mesh.process(validate=True)

    output_obj_path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(output_obj_path))
    log.info("Wrote decimated mesh: %d verts, %d faces", len(mesh.vertices), len(mesh.faces))
    return output_obj_path
