"""Diagnostic: list installed Blender extensions and try to enable the
'Export Paper Model' unfolder.

Run with::

    & "D:\\Blender\\blender.exe" --background --factory-startup --python blender_scripts\\probe_addon.py
"""
import addon_utils
import bpy

print("\n=== Installed addons / extensions ===")
modules = addon_utils.modules()
hits = []
for m in modules:
    name = m.__name__
    bl_info = addon_utils.module_bl_info(m) or {}
    label = bl_info.get("name", "?")
    if "paper" in name.lower() or "paper" in label.lower():
        hits.append((name, label))
        print(f"  [paper] module={name}  name='{label}'")

if not hits:
    print("  No 'paper' addon found.")

print("\n=== Try enabling each candidate ===")
candidates = [m[0] for m in hits] + [
    "io_export_paper_model",
    "bl_ext.blender_org.export_paper_model",
    "bl_ext.user_default.export_paper_model",
]
seen = set()
for cand in candidates:
    if cand in seen:
        continue
    seen.add(cand)
    try:
        bpy.ops.preferences.addon_enable(module=cand)
        print(f"  OK enabled: {cand}")
    except Exception as e:
        print(f"  FAIL {cand}: {type(e).__name__}: {e}")

print("\n=== Operators after enable ===")
print("  has export_mesh.paper_model:", hasattr(bpy.ops.export_mesh, "paper_model"))

print("\n=== Repos / extensions paths ===")
try:
    import bl_pkg  # type: ignore
    repos = list(bpy.context.preferences.extensions.repos) if hasattr(bpy.context.preferences, "extensions") else []
    for r in repos:
        print(f"  repo: name='{r.name}' module='{r.module}' enabled={r.enabled} dir={r.directory}")
except Exception as e:
    print(f"  (no extensions repo info: {e})")
