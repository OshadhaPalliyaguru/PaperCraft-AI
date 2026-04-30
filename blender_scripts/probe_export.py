"""List the valid parameters and enum values of the export_mesh.paper_model
operator so we know exactly how to call it.
"""
import bpy

bpy.ops.preferences.addon_enable(module="bl_ext.blender_org.export_paper_model")

op = bpy.ops.export_mesh.paper_model
rna = op.get_rna_type()

print("\n=== export_mesh.paper_model parameters ===\n")
for p in rna.properties:
    if p.identifier == "rna_type":
        continue
    line = f"  {p.identifier:30s}  type={p.type}"
    if hasattr(p, "default"):
        try:
            line += f"  default={p.default!r}"
        except Exception:
            pass
    if p.type == "ENUM":
        try:
            items = [it.identifier for it in p.enum_items]
            line += f"  values={items}"
        except Exception:
            pass
    print(line)
