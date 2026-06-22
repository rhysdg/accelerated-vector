"""
Configure all bones in the .blend with unique servo IDs and Vector motor types.

Usage (from terminal):
    blender /path/to/model.blend --background --python scripts/configure_servos.py

Or paste this into Blender's Scripting workspace and run it.
"""

import bpy
import sys
import os

# Add the addon root to sys.path and register it so the PointerProperty
# (Bone.servo_settings) is available and will serialise correctly.
ADDON_ROOT = os.path.join(os.path.dirname(__file__), "..", "vector_animation")
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, os.path.dirname(ADDON_ROOT))

from vector_animation import register, unregister

# ── Mapping: (armature_name, new_bone_name, servo_id, vector_motor) ────────
# Adjust these to match your rig's actual bone-to-Vector-part relationship.
# No proper wheel armatures exist yet — only Head, Lift, and Body rotation.
# (armature, bone_name, servo_id, motor, rotation_axis)
# rotation_axis: 0=X, 1=Y, 2=Z — set to match the axis the armature rotates
# on in Object Mode.  The current armatures all rotate primarily on Y.
SERVO_CONFIG = [
    ("Armature",     "Head",     1, "HEAD",      1),
    ("Armature.003", "Lift",     2, "LIFT",      1),
    ("Armature.001", "Body_Turn",5, "BODY_TURN", 1),
]


def configure():
    configured = 0
    for arm_name, bone_name, servo_id, motor_type, rot_axis in SERVO_CONFIG:
        arm_obj = bpy.data.objects.get(arm_name)
        if not arm_obj or arm_obj.type != 'ARMATURE':
            print(f"  ⚠  Armature '{arm_name}' not found, skipping")
            continue

        edit_bone = arm_obj.data.bones.get(bone_name) or arm_obj.data.bones[0] if arm_obj.data.bones else None
        if not edit_bone:
            print(f"  ⚠  No bones found in '{arm_name}', skipping")
            continue

        s = edit_bone.servo_settings

        # Assign
        edit_bone.name = bone_name
        s.active = True
        s.servo_id = servo_id
        s.vector_motor = motor_type
        s.rotation_axis = str(rot_axis)  # '0'=X, '1'=Y, '2'=Z

        # Sensible defaults per motor type
        if motor_type == "HEAD":
            s.position_min = 200
            s.position_max = 800
            s.neutral_angle = 90
            s.rotation_range = 90
            s.threshold = 30
        elif motor_type == "LIFT":
            s.position_min = 150
            s.position_max = 600
            s.neutral_angle = 90
            s.rotation_range = 90
            s.threshold = 20
        elif motor_type in ("LEFT_WHEEL", "RIGHT_WHEEL"):
            s.position_min = 0
            s.position_max = 1000
            s.neutral_angle = 90
            s.rotation_range = 180
            s.threshold = 50
        elif motor_type == "BODY_TURN":
            s.position_min = 0
            s.position_max = 1000
            s.neutral_angle = 90
            s.rotation_range = 360
            s.threshold = 40

        print(f"  ✓ {arm_name}/{bone_name} → servo_id={servo_id}, motor={motor_type}")
        configured += 1

    print(f"\nConfigured {configured} bone(s).")
    if configured:
        print("Don't forget to save: File → Save or Ctrl+S")


if __name__ == "__main__":
    # Register addon first so Bone.servo_settings PointerProperty is
    # available — the property must remain registered when saving for
    # Blender to properly serialise the sub‑property values.
    register()
    configure()

    print("\n⚠  The .blend has NOT been saved — this preserves your in‑session")
    print("   animation keyframes. Save manually from Blender (Ctrl+S) when ready.")

    unregister()

    if bpy.app.background:
        print("\n   To save from the terminal:")
        print("   1. Open the file in Blender normally")
        print("   2. Run Scripting workspace → paste/run this script")
        print("   3. File → Save (Ctrl+S)")
