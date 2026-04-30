"""Runs *inside* Blender via:

    blender -b -P blender_scripts/unfold.py -- <input.glb|.obj> <output.pdf|.svg> [page_size] [target_faces]

Imports the mesh (GLB preserves textures+UVs; OBJ falls back to plain
geometry), decimates it inside Blender so UVs survive, scrubs degenerate
faces/edges, marks seams along very sharp edges only (so islands stay
big), then runs the *Export Paper Model* exporter with TEXTURE output
when materials are present.
"""
import math
import os
import sys

import bmesh
import bpy

# ---------- args ----------
argv = sys.argv
argv = argv[argv.index("--") + 1:] if "--" in argv else []

if len(argv) < 2:
    print("[unfold] usage: blender -b -P unfold.py -- input.[glb|obj] output.pdf [page] [target_faces]")
    sys.exit(2)

input_path = os.path.abspath(argv[0])
output_path = os.path.abspath(argv[1])
page_preset = (argv[2] if len(argv) > 2 else "A4").upper()
target_faces = int(argv[3]) if len(argv) > 3 else 35

# Page name aliasing (the addon uses US_LETTER / US_LEGAL)
page_alias = {"LETTER": "US_LETTER", "USLETTER": "US_LETTER", "LEGAL": "US_LEGAL"}
page_preset = page_alias.get(page_preset, page_preset)
if page_preset not in ("A4", "A3", "US_LETTER", "US_LEGAL", "USER"):
    page_preset = "A4"

print(f"[unfold] in : {input_path}")
print(f"[unfold] out: {output_path}")
print(f"[unfold] page: {page_preset}, target_faces: {target_faces}")


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
        "[unfold] ERROR: 'Export Paper Model' addon is not installed.\n"
        "  Open Blender > Preferences > Get Extensions > install 'Export Paper Model'\n"
        f"  Last error: {last_err}"
    )
    sys.exit(3)


# ---------- import (GLB preserves materials/UVs) ----------
ext = os.path.splitext(input_path)[1].lower()
try:
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=input_path)
    elif ext == ".obj":
        try:
            bpy.ops.wm.obj_import(filepath=input_path)
        except AttributeError:
            bpy.ops.import_scene.obj(filepath=input_path)
    else:
        print(f"[unfold] ERROR: unsupported input format: {ext}")
        sys.exit(4)
except Exception as e:
    print(f"[unfold] ERROR importing {input_path}: {e}")
    sys.exit(4)

mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not mesh_objs:
    print("[unfold] ERROR: no mesh imported")
    sys.exit(4)

# ---------- pick / join meshes and set active ----------
bpy.ops.object.select_all(action="DESELECT")
for o in mesh_objs:
    o.select_set(True)
bpy.context.view_layer.objects.active = mesh_objs[0]

if len(mesh_objs) > 1:
    bpy.ops.object.join()

obj = bpy.context.view_layer.objects.active
if obj is None:
    # Fallback: glTF import in some Blender builds doesn't update the
    # active object even after we set it. Pull directly from the scene.
    candidates = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not candidates:
        print("[unfold] ERROR: lost mesh after join")
        sys.exit(4)
    obj = candidates[0]
    bpy.context.view_layer.objects.active = obj

bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.context.view_layer.objects.active = obj

has_uvs = bool(obj.data.uv_layers)
has_materials = bool(obj.data.materials and obj.data.materials[0] is not None)
print(
    f"[unfold] imported: verts={len(obj.data.vertices)} faces={len(obj.data.polygons)} "
    f"uvs={has_uvs} materials={has_materials}"
)


# ---------- center + scale to printable size ----------
# Recenter origin to geometry center, then scale so the model is ~14 cm tall.
bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
obj.location = (0, 0, 0)

TARGET_HEIGHT_M = 0.14
dims = obj.dimensions
max_dim = max(dims.x, dims.y, dims.z) or 1.0
scale = TARGET_HEIGHT_M / max_dim
obj.scale = (scale, scale, scale)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


# ---------- voxel remesh for guaranteed-manifold mesh ----------
# AI meshes (Hunyuan3D, TRELLIS, etc.) almost always contain non-manifold
# edges, internal walls, and tiny holes. The paper-model exporter rejects
# any of those. Voxel remesh rebuilds the mesh as a watertight surface.
# WARNING: it discards UVs and materials, so we only apply it on the
# shape-only path (`has_uvs == False`). For the textured path we'd need
# a different cleanup that preserves UVs.
if not has_uvs:
    new_max_dim = max(obj.dimensions.x, obj.dimensions.y, obj.dimensions.z) or TARGET_HEIGHT_M
    voxel_size = new_max_dim / 80.0
    print(f"[unfold] voxel remesh: voxel_size={voxel_size:.5f}m (~80 voxels/dim)")
    mod = obj.modifiers.new(name="Remesh", type="REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = voxel_size
    mod.use_smooth_shade = False
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
        print(f"[unfold] after voxel remesh: {len(obj.data.polygons)} faces (manifold)")
    except Exception as e:
        print(f"[unfold] voxel remesh failed (non-fatal): {e}")
        try:
            obj.modifiers.remove(mod)
        except Exception:
            pass


# ---------- decimate via modifier (preserves UVs and materials) ----------
# Iterative: AI meshes are often huge (200k-500k faces) so a single
# tiny-ratio pass can leave artefacts. We loop until the face count
# is within ~25% of target_faces, capping at 6 attempts.
def _apply_decimate(target_obj, ratio):
    mod = target_obj.modifiers.new(name="Decimate", type="DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True  # also kills non-planar quads
    bpy.ops.object.modifier_apply(modifier=mod.name)


for attempt in range(6):
    n_faces = len(obj.data.polygons)
    if n_faces <= int(target_faces * 1.25):
        break
    ratio = min(1.0, max(0.001, target_faces / float(n_faces)))
    _apply_decimate(obj, ratio)
    print(
        f"[unfold] decimate pass {attempt}: {n_faces} -> {len(obj.data.polygons)} "
        f"faces (ratio={ratio:.4f})"
    )


# ---------- clean degenerate geometry ----------
def _bmesh_purge(target_obj, area_eps=1e-7, len_eps=1e-5):
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

    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    bm.to_mesh(target_obj.data)
    target_obj.data.update()
    bm.free()
    return len(bad_faces), len(bad_edges), len(loose)


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
    nf, ne, nv = _bmesh_purge(obj, area_eps=area_eps, len_eps=len_eps)
    print(f"[unfold] cleanup pass {pass_idx}: removed faces={nf} edges={ne} loose={nv}")
    if nf == 0 and ne == 0 and nv == 0 and pass_idx >= 1:
        break

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
try:
    bpy.ops.mesh.normals_make_consistent(inside=False)
except Exception as e:
    print(f"[unfold] normals warn: {e}")
bpy.ops.object.mode_set(mode="OBJECT")

if len(obj.data.polygons) < 6:
    print(f"[unfold] ERROR: cleanup left only {len(obj.data.polygons)} faces.")
    sys.exit(6)


# ---------- non-manifold safety pass ----------
# Voxel remesh + decimation usually leaves the mesh manifold, but very
# aggressive decimation can re-introduce small holes or non-manifold
# edges. We try to fill small holes, then dissolve any edges that are
# still non-manifold so the unfolder doesn't see edge.angle == None.
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_mode(type="EDGE")
bpy.ops.mesh.select_all(action="DESELECT")
try:
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.fill_holes(sides=12)
except Exception as e:
    print(f"[unfold] fill_holes warn: {e}")

bpy.ops.mesh.select_all(action="DESELECT")
try:
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.dissolve_edges(use_verts=True, use_face_split=False)
except Exception as e:
    print(f"[unfold] dissolve non-manifold warn: {e}")
bpy.ops.mesh.select_all(action="DESELECT")
bpy.ops.object.mode_set(mode="OBJECT")
print(f"[unfold] post-manifold cleanup: {len(obj.data.polygons)} faces")


# ---------- triangulate so the unfolder never sees twisted polygons ----------
# Decimation can leave non-planar quads which the paper-model exporter
# rejects as "twisted". Forcing all faces to triangles eliminates that
# class of error entirely.
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
try:
    bpy.ops.mesh.quads_convert_to_tris(
        quad_method="BEAUTY",
        ngon_method="BEAUTY",
    )
    print(f"[unfold] triangulated: {len(obj.data.polygons)} faces")
except Exception as e:
    print(f"[unfold] triangulate warn: {e}")
bpy.ops.object.mode_set(mode="OBJECT")


# ---------- mark seams only on very sharp edges so islands stay BIG ----------
# 30 deg = lots of cuts = lots of small islands (the previous mess).
# 70 deg = only cuts where the geometry really folds back on itself.
SEAM_ANGLE_DEG = 70
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_mode(type="EDGE")
bpy.ops.mesh.select_all(action="DESELECT")
try:
    bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(SEAM_ANGLE_DEG))
    bpy.ops.mesh.mark_seam(clear=False)
    print(f"[unfold] marked seams on edges sharper than {SEAM_ANGLE_DEG} deg")
except Exception as e:
    print(f"[unfold] sharp-seam pass failed: {e}")
bpy.ops.mesh.select_all(action="DESELECT")
bpy.ops.object.mode_set(mode="OBJECT")


# ---------- run the paper-model exporter ----------
ext = os.path.splitext(output_path)[1].lower()
file_format = "PDF" if ext == ".pdf" else "SVG"

# If the mesh has UVs + materials we try TEXTURE; else NONE outline.
texture_type = "TEXTURE" if (has_uvs and has_materials) else "NONE"

base_args = dict(
    filepath=output_path,
    page_size_preset=page_preset,
    sticker_width=0.005,           # 5 mm glue tab
    paper_thickness=0.0001,
    angle_epsilon=math.radians(1),
    output_dpi=200,
    file_format=file_format,
    tab_style="STICKER",
    number_style="INSIDE",
    image_packing="ISLAND_EMBED",
    bake_samples=8,
    do_create_uvmap=False,
    output_layers="ONESIDE",
    nesting_method="BOX",
)

# Layered fallbacks. The addon has known bugs (e.g. UVEdge.neighbor_left
# during sticker generation) that depend on mesh topology, plus the
# texture *bake* path is fragile. Each fallback peels off one feature so
# we always produce SOME PDF.
if has_uvs and has_materials:
    attempts = [
        {"texture_type": "TEXTURE", "tab_style": "STICKER", "number_style": "INSIDE"},
        {"texture_type": "RENDER",  "tab_style": "STICKER", "number_style": "INSIDE"},
        {"texture_type": "TEXTURE", "tab_style": "NONE",    "number_style": "INSIDE"},
        {"texture_type": "RENDER",  "tab_style": "NONE",    "number_style": "INSIDE"},
        {"texture_type": "NONE",    "tab_style": "STICKER", "number_style": "INSIDE"},
        {"texture_type": "NONE",    "tab_style": "NONE",    "number_style": "INSIDE"},
        {"texture_type": "NONE",    "tab_style": "NONE",    "number_style": "NONE"},
    ]
else:
    attempts = [
        {"texture_type": "NONE", "tab_style": "STICKER", "number_style": "INSIDE"},
        {"texture_type": "NONE", "tab_style": "NONE",    "number_style": "INSIDE"},
        {"texture_type": "NONE", "tab_style": "NONE",    "number_style": "NONE"},
    ]

last_err = None
exported = False
for i, override in enumerate(attempts):
    args = {**base_args, **override}
    print(
        f"[unfold] export attempt {i+1}: "
        f"texture={args['texture_type']} tabs={args['tab_style']} numbers={args['number_style']}"
    )
    # Make sure we don't accidentally read a stale file from a previous attempt.
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception:
        pass
    try:
        result = bpy.ops.export_mesh.paper_model(**args)
        print(f"[unfold] exporter result: {result}")
        if os.path.exists(output_path):
            exported = True
            print(f"[unfold] export attempt {i+1} succeeded.")
            break
    except Exception as e:
        # Strip noisy tracebacks; keep the message.
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        print(f"[unfold] export attempt {i+1} failed: {msg}")
        last_err = e

if not exported:
    print(f"[unfold] ERROR: all export attempts failed. Last error: {last_err}")
    sys.exit(5)

print(f"[unfold] OK -> {output_path}")
