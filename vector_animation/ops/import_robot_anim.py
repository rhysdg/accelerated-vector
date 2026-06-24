"""
Blender operator to import Vector robot animations (.bin files) as Blender keyframes.

Fetches the list of animations from the robot via SSH using the WireOS key,
lets the user select one, then parses the .bin file and creates keyframes on
the active armature bones.
"""

import os
import subprocess
import tempfile
import json
from pathlib import Path

import bpy
from bpy.types import Operator, Panel
from bpy.props import StringProperty, EnumProperty

from ..utils.bin_parser import parse_bin_file, extract_blender_keyframes, parse_anim_manifest


# ── Configuration ──────────────────────────────────────────────────────────
# These should ideally come from addon preferences, but for now use defaults
DEFAULT_SSH_KEY = os.path.expanduser("~/.ssh/wireos_root_key")
DEFAULT_BOT_IP = "192.168.0.15"
DEFAULT_PI_IP = "192.168.0.12"
ANIM_DIR = "/anki/data/assets/cozmo_resources/assets/animations/"
MANIFEST_PATH = "/anki/data/assets/cozmo_resources/assets/anim_manifest.json"


def _ssh_exec(ip, cmd):
    """Run a command on the robot via SSH using the WireOS key."""
    key = os.path.expanduser(DEFAULT_SSH_KEY)
    if not os.path.exists(key):
        return f"SSH key not found at {key}"
    ssh_cmd = [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        f"root@{ip}",
        cmd,
    ]
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return f"SSH error: {result.stderr.strip()}"
        return result.stdout.strip()
    except FileNotFoundError:
        return "SSH not found — is it installed?"
    except subprocess.TimeoutExpired:
        return "SSH timed out — is the robot reachable?"
    except Exception as e:
        return f"SSH failed: {e}"


def _list_anim_files():
    """Return list of .bin filenames from the robot."""
    raw = _ssh_exec(DEFAULT_BOT_IP, f"ls {ANIM_DIR}")
    if raw.startswith("SSH error") or raw.startswith("SSH not found") or raw.startswith("SSH timed out"):
        return []
    return [line.strip() for line in raw.splitlines() if line.strip().endswith('.bin')]


def _download_anim_file(filename, dest_path):
    """SCP an animation .bin file from the robot to a local path."""
    key = os.path.expanduser(DEFAULT_SSH_KEY)
    src = f"root@{DEFAULT_BOT_IP}:{ANIM_DIR}{filename}"
    scp_cmd = [
        "scp", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        src, dest_path,
    ]
    try:
        subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30)
        return True
    except:
        return False


def _get_anim_manifest():
    """Download and parse the anim_manifest.json from the robot."""
    key = os.path.expanduser(DEFAULT_SSH_KEY)
    src = f"root@{DEFAULT_BOT_IP}:{MANIFEST_PATH}"
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        dest = f.name
    scp_cmd = [
        "scp", "-i", key,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        src, dest,
    ]
    try:
        subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30)
        with open(dest) as f:
            manifest = json.load(f)
        os.unlink(dest)
        return {e['name']: e['length_ms'] for e in manifest}
    except:
        try:
            os.unlink(dest)
        except:
            pass
        return {}


def _get_bones_for_motor(motor_type):
    """Find pose bones in the scene that are tagged with a specific motor type."""
    bones = []
    for obj in bpy.data.objects:
        if obj.type != 'ARMATURE':
            continue
        for pbone in obj.pose.bones:
            if pbone.bone.servo_settings.vector_motor == motor_type:
                bones.append((obj, pbone))
    return bones


# ── Operators ──────────────────────────────────────────────────────────────

class AnimOT_RefreshRobotAnims(Operator):
    """Refresh the list of animations from the robot"""
    bl_idname = "anim.refresh_robot_anims"
    bl_label = "Refresh Robot Animation List"
    bl_description = "SSH into the robot and list available .bin animations"
    
    def execute(self, context):
        wm = context.window_manager
        servo_anim = wm.servo_animation
        
        files = _list_anim_files()
        manifest = _get_anim_manifest()
        
        # Build human-readable names sorted by duration
        anim_entries = []
        for fname in sorted(files):
            base = fname.replace('.bin', '')
            dur = manifest.get(base, '?')
            if isinstance(dur, int) and dur > 0:
                label = f"{base} ({dur}ms)"
            else:
                label = base
            anim_entries.append((fname, label))
        
        # Store as a temporary JSON so the enum property can read it
        wm.servo_animation.robot_anim_list_json = json.dumps(anim_entries)
        wm.servo_animation.robot_anim_count = len(anim_entries)
        
        self.report({'INFO'}, f"Found {len(anim_entries)} animations on robot")
        return {'FINISHED'}


class AnimOT_ImportRobotAnim(Operator):
    """Import a selected .bin animation as Blender keyframes"""
    bl_idname = "anim.import_robot_anim"
    bl_label = "Import Vector Animation"
    bl_description = "Download the selected animation and create keyframes on the armature"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        wm = context.window_manager
        servo_anim = wm.servo_animation
        selected = servo_anim.robot_anim_select
        
        if not selected:
            self.report({'ERROR'}, "No animation selected")
            return {'CANCELLED'}
        
        # Download the .bin file
        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f:
            local_path = f.name
        
        if not _download_anim_file(selected, local_path):
            self.report({'ERROR'}, f"Failed to download {selected}")
            try:
                os.unlink(local_path)
            except:
                pass
            return {'CANCELLED'}
        
        # Parse it using FlatBuffers
        try:
            parsed_data = parse_bin_file(local_path)
            parsed = extract_blender_keyframes(parsed_data)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to parse .bin: {e}")
            import traceback
            traceback.print_exc()
            try:
                os.unlink(local_path)
            except:
                pass
            return {'CANCELLED'}
        
        # Now create keyframes on the armatures
        if not parsed:
            self.report({'WARNING'}, "No keyframes found in animation")
            try:
                os.unlink(local_path)
            except:
                pass
            return {'CANCELLED'}
        
        scene = context.scene
        created_count = 0
        reported_motors = set()
        current_frame = scene.frame_start
        
        # parsed is now a dict: {motor_type: [keyframe_entries]}
        for motor, kfs in parsed.items():
            if not kfs:
                continue
            
            # Find the armature bone for this motor
            bone_pairs = _get_bones_for_motor(motor)
            if not bone_pairs:
                if motor not in reported_motors:
                    self.report({'WARNING'}, f"No armature bone found for motor {motor}")
                    reported_motors.add(motor)
                continue
            
            # Create keyframes on the first matching armature bone
            arm_obj, pbone = bone_pairs[0]
            
            # Clear existing animation data
            if not arm_obj.animation_data:
                arm_obj.animation_data_create()
            
            # Compute the rotation value for each keyframe
            for kf in kfs:
                duration = kf.get('duration_ms', 33)
                
                # Convert from SDK units to Blender rotation
                if motor == 'HEAD':
                    # angle_deg from .bin keyframe is the SDK head angle (-22° to +45°)
                    # Maya rig head_jnt rotates around X axis.
                    # Map: SDK -22° (down) → -22° X, SDK 45° (up) → 45° X
                    angle_deg = kf.get('angle_deg', 0)
                    rot_y = angle_deg  # Direct mapping to X-axis rotation
                elif motor == 'LIFT':
                    # height_mm is the lift height in 0-255mm from the FlatBuffers format
                    height_mm = kf.get('height_mm', 0)
                    rot_y = height_mm * (45.0 / 255.0)  # Map 0-255mm to 0-45° rotation
                else:
                    rot_y = 0
                
                # Set the armature rotation and insert keyframe
                # Maya rig uses rotation_euler[0] (X axis) for HEAD rotation_euler[1] (Y) for LIFT/BODY
                if motor == 'HEAD':
                    arm_obj.rotation_euler[0] = rot_y
                    arm_obj.keyframe_insert(data_path="rotation_euler", index=0, frame=current_frame)
                else:
                    arm_obj.rotation_euler[1] = rot_y
                    arm_obj.keyframe_insert(data_path="rotation_euler", index=1, frame=current_frame)
                created_count += 1
                
                # Advance frame based on duration
                frame_step = max(1, round(duration / 33))  # 33ms per frame at ~30fps
                current_frame += frame_step
        
        # Clean up
        try:
            os.unlink(local_path)
        except:
            pass
        
        # Update scene frame range
        scene.frame_end = max(scene.frame_end, current_frame)
        
        self.report({'INFO'}, f"Imported {selected}: {created_count} keyframes created")
        return {'FINISHED'}


# ── UI Panel ───────────────────────────────────────────────────────────────

class AnimPT_RobotAnimPanel(Panel):
    bl_label = "Vector Animation Importer"
    bl_idname = "VIEW3D_PT_vector_anim_importer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Servo Animation"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        return True
    
    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        servo_anim = wm.servo_animation
        
        col = layout.column(align=True)
        col.prop(servo_anim, "robot_anim_select", text="Animation")
        
        row = col.row(align=True)
        row.operator("anim.refresh_robot_anims", text="Refresh List")
        row.operator("anim.import_robot_anim", text="Import → Keyframes")


# ── Property Group ─────────────────────────────────────────────────────────

class RobotAnimProperties:
    """Mixin-like property definitions added to WindowManagerPropertyGroup"""
    pass


def update_anim_enum(self, context):
    """Dynamically populate the animation enum from stored JSON."""
    import json
    items = []
    
    try:
        raw = context.window_manager.servo_animation.robot_anim_list_json
        entries = json.loads(raw) if raw else []
    except:
        entries = []
    
    for fname, label in entries:
        items.append((fname, label, ""))
    
    if not items:
        items.append(("NONE", "No animations found", "Refresh the list"))
    
    return items


# ── Registration helpers ───────────────────────────────────────────────────

# ── LP Mesh Visibility Toggle ─────────────────────────────────────────────

class AnimOT_ToggleLPMeshes(Operator):
    """Toggle visibility of low-poly (LP_) meshes in the scene"""
    bl_idname = "anim.toggle_lp_meshes"
    bl_label = "Toggle Low-Poly Shell"
    bl_description = "Show/hide the low-poly (LP_) proxy meshes"
    
    def execute(self, context):
        lp_vis = None
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.name.startswith('LP_'):
                if lp_vis is None:
                    lp_vis = obj.hide_get()
                obj.hide_set(not lp_vis)
        if lp_vis is None:
            self.report({'INFO'}, "No LP meshes in scene")
        else:
            state = "HIDDEN" if lp_vis else "VISIBLE"
            self.report({'INFO'}, f"LP meshes now {state}")
        return {'FINISHED'}


CLASSES = [
    AnimOT_RefreshRobotAnims,
    AnimOT_ImportRobotAnim,
    AnimPT_RobotAnimPanel,
    AnimOT_ToggleLPMeshes,
]
