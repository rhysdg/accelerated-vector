"""
Rebuild the Vector rig into a single armature.

Strategy:
  - Create ONE armature "Vector" at world origin
  - Place a bone at each old armature's world position
  - OBJECT-parent each mesh to the new armature (preserving world pos via
    parent_inverse)
  - Add Armature modifier + vertex group so the mesh rigidly follows its bone
  - Remove old armatures + empties
  - Configure servo settings

Run from terminal:
    blender blender_model/accelerate_vector.blend \\
        --background --python scripts/rebuild_rig.py
"""

import sys, math
from mathutils import Vector
import bpy
import os

ADDON = os.path.join(os.path.dirname(__file__), "..", "vector_animation")
if os.path.dirname(ADDON) not in sys.path:
    sys.path.insert(0, os.path.dirname(ADDON))
from vector_animation import register as reg, unregister as unreg

# ── Bone definitions ─────────────────────────────────────────────────────
BONE_DEFS = [
    # (name,         parent,      old_armature_name,  length)
    ("Body",         None,        None,                0.5),
    ("Body_Turn",    "Body",      None,                0.5),
    ("Head",         "Body_Turn", "Armature",          0.5),
    ("Lift",         "Body_Turn", "Armature.001",      0.5),
    ("Left_Wheel",   "Body_Turn", "Armature.002",      0.5),
    ("Right_Wheel",  "Body_Turn", "Armature.003",      0.5),
]

# Mesh → target bone
MESH_MAP = {
    "Cube":        "Head",
    "Plane.003":   "Lift",
    "Plane":       "Left_Wheel",
    "Plane.001":   "Right_Wheel",
    "Plane.006":   "Left_Wheel",
    "Plane.007":   "Left_Wheel",
    "Plane.009":   "Left_Wheel",
    "Cylinder.001":"Left_Wheel",
    "Cylinder.003":"Right_Wheel",
    "Plane.002":   "Body",
    "Plane.004":   "Body",
    "Plane.005":   "Body",
    "Plane.008":   "Body",
}

SERVO_CFG = {
    "Body":       (0, "LIFT",       0, 1000, 90, 180, 50),
    "Body_Turn":  (5, "BODY_TURN",  0, 1000, 90, 360, 40),
    "Head":       (1, "HEAD",     200,  800, 90,  90, 30),
    "Lift":       (2, "LIFT",     150,  600, 90,  90, 20),
    "Left_Wheel": (3, "LEFT_WHEEL",0, 1000, 90, 180, 50),
    "Right_Wheel":(4, "RIGHT_WHEEL",0,1000, 90, 180, 50),
}


def run():
    reg()
    scene = bpy.context.scene
    view = bpy.context.view_layer

    # ── 1.  Save mesh world matrices ─────────────────────────────────────
    mesh_world = {o.name: o.matrix_world.copy()
                  for o in bpy.data.objects if o.type == 'MESH'}
    old_arm_loc = {o.name: o.location.copy()
                   for o in bpy.data.objects if o.type == 'ARMATURE'}

    # ── 2.  Create new armature ──────────────────────────────────────────
    arm_data = bpy.data.armatures.new("Vector")
    arm_obj = bpy.data.objects.new("Vector", arm_data)
    scene.collection.objects.link(arm_obj)
    view.objects.active = arm_obj

    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_data.edit_bones

    created = {}
    for name, parent_name, arm_name, length in BONE_DEFS:
        ebone = edit_bones.new(name)
        ebone.length = length
        if arm_name and arm_name in old_arm_loc:
            ebone.head = old_arm_loc[arm_name]
        else:
            # Place root/support bones based on parent
            if parent_name and parent_name in created:
                ebone.head = created[parent_name].tail.copy()
        ebone.tail = ebone.head + Vector((0, length, 0))
        created[name] = ebone

    for name, parent_name, arm_name, length in BONE_DEFS:
        if parent_name and parent_name in created:
            created[name].parent = created[parent_name]

    bpy.ops.object.mode_set(mode='OBJECT')
    view.update()

    # ── 3.  Reparent meshes: OBJECT parent + Armature modifier ───────────
    for mesh_name, bone_name in MESH_MAP.items():
        mesh = bpy.data.objects.get(mesh_name)
        if not mesh or mesh.type != 'MESH':
            continue
        world = mesh_world.get(mesh_name)
        if world is None:
            continue

        # Remove ALL old Armature modifiers (from original file)
        for mod in list(mesh.modifiers):
            if mod.type == 'ARMATURE':
                mesh.modifiers.remove(mod)

        # Remove ALL old vertex groups
        for vg in list(mesh.vertex_groups):
            mesh.vertex_groups.remove(vg)

        # BONE-parent to the target bone — Blender auto-computes the
        # correct matrix_parent_inverse to preserve world position.
        mesh.parent = arm_obj
        mesh.parent_type = 'BONE'
        mesh.parent_bone = bone_name

        print(f"  ✓ {mesh_name} → bone '{bone_name}'")

    # ── 4.  Remove old armatures + empties ───────────────────────────────
    for obj in list(bpy.data.objects):
        if obj.type == 'ARMATURE' and obj.name != 'Vector':
            bpy.data.objects.remove(obj, do_unlink=True)
        elif obj.type == 'EMPTY':
            bpy.data.objects.remove(obj, do_unlink=True)

    # ── 5.  Configure servo settings ─────────────────────────────────────
    for bone_name, (sid, motor, pmin, pmax, neutral, rrange, thresh) in SERVO_CFG.items():
        b = arm_data.bones.get(bone_name)
        if not b:
            continue
        s = b.servo_settings
        s.active = True
        s.servo_id = sid
        s.vector_motor = motor
        s.position_min = pmin
        s.position_max = pmax
        s.neutral_angle = neutral
        s.rotation_range = rrange
        s.threshold = thresh

    # ── 6.  Verify ───────────────────────────────────────────────────────
    errors = 0
    for mesh in bpy.data.objects:
        if mesh.type == 'MESH' and mesh.name in mesh_world:
            shift = (mesh.matrix_world.translation - mesh_world[mesh.name].translation).length
            if shift > 0.001:
                print(f"  ⚠  '{mesh.name}' shifted by {shift:.4f}")
                errors += 1

    if errors == 0:
        print(f"\n✓ All {len(mesh_world)} meshes at original positions. {len(arm_data.bones)} bones.")
    else:
        print(f"\n⚠ {errors} mesh(es) shifted — restore backup and fix.")

    # Save BEFORE unregistering so PointerProperty data persists.
    if bpy.app.background:
        bpy.ops.wm.save_mainfile()
        print(f"Saved: {bpy.data.filepath}")

    unreg()


if __name__ == "__main__":
    run()
