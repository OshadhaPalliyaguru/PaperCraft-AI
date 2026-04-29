"""Runs *inside* Blender via:
    blender -b -P blender_scripts/unfold.py -- <input.obj> <output.pdf|.svg> [page_size]

Imports the OBJ, marks all sharp/angled edges as seams (so the unfolder
has cuts to work with), enables the built-in "Export Paper Model"
addon, and writes a printable PDF or SVG with cut + fold lines and
glue tabs.
"""
import math
import os
import sys

import bmesh
import bpy

# ---------- args ----------
argv = sys.argv
if "--" in argv:
    argv = argv[argv.index("--") + 1:]
else:
    argv = []

if len(argv) < 2:
    print("[unfold] usage: blender -b -P unfold.py -- input.obj output.pdf [page]")
    sys.exit(2)

input_path = os.path.abspath(argv[0])
output_path = os.path.abspath(argv[1])
page_preset = (argv[2] if len(argv) > 2 else "A4").upper()
if page_preset not in ("A4", "LETTER", "A3"):
    page_preset = "A4"

print(f"[unfold] in : {input_path}")
print(f"[unfold] out: {output_path}")
print(f"[unfold] page: {page_preset}")


# ---------- fresh scene ----------
bpy.ops.wm.read_factory_settings(use_empty=True)


# ---------- enable Export Paper Model addon ----------
ADDON_CANDIDATES = [
    "bl_ext.blender_org.export_paper_model",
    "bl_ext.user_default.export_paper_model",
    "io_export_paper_model",
]

addon_loaded = False
last_err = None
for mod in ADDON_CANDIDATES:
    try:
        bpy.ops.preferences.addon_enable(module=mod)
        addon_loaded = True
        print(f"[unfold] enabled addon: {mod}")
        break
    except Exception as e:
        last_err = e

if not addon_loaded:
    print(
        "[unfold] ERROR: 'Export Paper Model' addon is not available.\n"
        "  Install it in Blender:\n"
        "    Edit > Preferences > Get Extensions > search 'Export Paper Model'\n"
        "    OR Add-ons (legacy) > enable 'Import-Export: Export Paper Model'\n"
        f"  Last error: {last_err}"
    )
    sys.exit(3)


# ---------- import OBJ ----------
try:
    bpy.ops.wm.obj_import(filepath=input_path)
except AttributeError:
    bpy.ops.import_scene.obj(filepath=input_path)

mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not mesh_objs:
    print("[unfold] ERROR: no mesh imported")
    sys.exit(4)

obj = mesh_objs[0]
bpy.context.view_layer.objects.active = obj
for o in bpy.context.scene.objects:
    o.select_set(False)
obj.select_set(True)
print(f"[unfold] imported mesh: {obj.name} verts={len(obj.data.vertices)} faces={len(obj.data.polygons)}")


# ---------- scale to a sensible printable size ----------
# After our trimesh normalize step the mesh has max-extent = 1.0 in
# Blender units (= 1 metre by default). We rescale so the model is
# ~12 cm tall, which fits an A4 sheet and gives a comfortable build size.
TARGET_HEIGHT_M = 0.12
dims = obj.dimensions
max_dim = max(dims.x, dims.y, dims.z) or 1.0
scale = TARGET_HEIGHT_M / max_dim
obj.scale = (scale, scale, scale)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


# ---------- clean degenerate geometry ----------
# AI meshes plus decimation often leave zero-length edges, zero-area
# faces, and duplicate vertices. The paper-model exporter is strict
# about these so we hammer the mesh with several passes.

def _bmesh_purge(target_obj, area_eps=1e-7, len_eps=1e-5):
    """Explicit bmesh pass that deletes any face below ``area_eps`` and
    collapses any edge below ``len_eps``. More reliable than the bpy.ops
    'dissolve_degenerate' which can leave slivers."""
    bm = bmesh.new()
    bm.from_mesh(target_obj.data)

    bad_faces = [f for f in bm.faces if f.calc_area() < area_eps]
    if bad_faces:
        bmesh.ops.delete(bm, geom=bad_faces, context="FACES")
        bm.faces.ensure_lookup_table()

    bad_edges = [e for e in bm.edges if e.calc_length() < len_eps]
    if bad_edges:
        bmesh.ops.collapse(bm, edges=bad_edges, uvs=False)
        bm.edges.ensure_lookup_table()

    loose_verts = [v for v in bm.verts if not v.link_faces]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")

    bm.to_mesh(target_obj.data)
    target_obj.data.update()
    bm.free()
    return len(bad_faces), len(bad_edges), len(loose_verts)


# Run cleanup with progressively larger thresholds until nothing changes.
for pass_idx, (merge_dist, area_eps, len_eps) in enumerate(
    [(0.0005, 1e-7, 1e-5), (0.001, 1e-6, 1e-4), (0.002, 1e-5, 5e-4)]
):
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        bpy.ops.mesh.remove_doubles(threshold=merge_dist)
    except Exception as e:
        print(f"[unfold] remove_doubles warn pass {pass_idx}: {e}")
    try:
        bpy.ops.mesh.dissolve_degenerate(threshold=merge_dist)
    except Exception as e:
        print(f"[unfold] dissolve_degenerate warn pass {pass_idx}: {e}")
    try:
        bpy.ops.mesh.delete_loose()
    except Exception as e:
        print(f"[unfold] delete_loose warn pass {pass_idx}: {e}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Heavy hammer: explicit per-face / per-edge area & length check.
    nf, ne, nv = _bmesh_purge(obj, area_eps=area_eps, len_eps=len_eps)
    print(
        f"[unfold] cleanup pass {pass_idx}: removed faces={nf} collapsed edges={ne} loose verts={nv}"
    )
    if nf == 0 and ne == 0 and nv == 0 and pass_idx >= 1:
        break

# Final normals fix
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
try:
    bpy.ops.mesh.normals_make_consistent(inside=False)
except Exception as e:
    print(f"[unfold] normals warn: {e}")
bpy.ops.object.mode_set(mode="OBJECT")

# If we ended up with too few faces to be useful, abort with a clear msg.
if len(obj.data.polygons) < 6:
    print(
        f"[unfold] ERROR: cleanup left only {len(obj.data.polygons)} faces. "
        "The input mesh was too noisy. Try a different image, or raise TARGET_FACES."
    )
    sys.exit(6)

print(
    f"[unfold] cleaned mesh: verts={len(obj.data.vertices)} faces={len(obj.data.polygons)}"
)


# ---------- mark seams along sharp edges so the unfolder has cuts ----------
# Without seams, Export Paper Model treats the whole mesh as one giant
# island and produces unusable output. Marking edges sharper than ~30°
# gives clean, separable patches.
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="DESELECT")
bpy.ops.mesh.select_mode(type="EDGE")
bpy.ops.mesh.select_all(action="SELECT")
try:
    bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(30))
    bpy.ops.mesh.mark_seam(clear=False)
except Exception as e:
    print(f"[unfold] sharp-edge seam pass failed: {e}")
bpy.ops.object.mode_set(mode="OBJECT")


# ---------- run the paper-model exporter ----------
ext = os.path.splitext(output_path)[1].lower()
file_format = "PDF" if ext == ".pdf" else "SVG"

# Operator parameters can vary slightly across addon versions. Build a
# dict of "preferred" args and drop any the operator does not support.
preferred = dict(
    filepath=output_path,
    page_size_preset=page_preset,
    output_type="NONE",          # plain unfold, no texture
    do_create_stickers=True,     # glue tabs
    sticker_width=0.005,         # 5 mm tabs
    file_format=file_format,
    output_dpi=300,
    angle_epsilon=math.radians(1),
    do_create_numbers=True,
)

op = bpy.ops.export_mesh.paper_model
try:
    rna = op.get_rna_type()
    valid = {p.identifier for p in rna.properties}
    kwargs = {k: v for k, v in preferred.items() if k in valid}
except Exception:
    kwargs = preferred

print(f"[unfold] export args: {kwargs}")
result = op(**kwargs)
print(f"[unfold] exporter result: {result}")

if not os.path.exists(output_path):
    print(f"[unfold] ERROR: output file not written: {output_path}")
    sys.exit(5)

print(f"[unfold] OK -> {output_path}")
