import math
import mathutils


def range_map(value, from_low, from_high, to_low, to_high):
    return (value - from_low) * (to_high - to_low) / (from_high - from_low) + to_low


# ── Cached rest‑pose matrix data ───────────────────────────────────────────
# ``matrix_bone_inverted`` (inverse of bone.matrix_local) and
# ``matrix_parent_bone`` (bone.parent.matrix_local) are invariant during
# animation — they depend only on the armature's rest pose.  We pre‑compute
# them once and reuse across all frame evaluations.
#
# ``mat_parent_pose_inv`` (inverse of parent's *pose* matrix) is **not**
# cached — it changes every frame when the parent bone is animated.
#
# Cache entry per bone:  (mat_bone_inv, mat_parent_bone)

_matrix_cache = {}  # {bone_name: (Matrix, Matrix)}


def cache_bone_matrices(pose_bones):
    """Pre‑compute and cache rest‑pose matrix inverses for a list of bones."""
    global _matrix_cache
    _matrix_cache.clear()
    for pose_bone in pose_bones:
        bone = pose_bone.bone
        mat_bone_inv = bone.matrix_local.inverted()
        mat_parent_bone = (
            bone.parent.matrix_local
            if bone.parent
            else mathutils.Matrix()
        )
        _matrix_cache[bone.name] = (mat_bone_inv, mat_parent_bone)


def clear_matrix_cache():
    global _matrix_cache
    _matrix_cache.clear()


def matrix_visual(pose_bone):
    """Decompose a pose bone's transform relative to the armature.

    Skips matrix inversion of rest‑pose data when the cache is warm
    (see :func:`cache_bone_matrices`).
    """
    bone = pose_bone.bone
    key = bone.name

    cached = _matrix_cache.get(key)
    if cached is not None:
        mat_bone_inv, mat_parent_bone = cached
    else:
        mat_bone_inv = bone.matrix_local.inverted()
        mat_parent_bone = (
            bone.parent.matrix_local
            if bone.parent
            else mathutils.Matrix()
        )

    # Parent pose matrix — frame-dependent, must compute fresh
    mat_parent_pose_inv = (
        pose_bone.parent.matrix.inverted_safe()
        if bone.parent
        else mathutils.Matrix()
    )

    return mat_bone_inv @ mat_parent_bone @ mat_parent_pose_inv @ pose_bone.matrix


def calculate_position(pose_bone, precision):
    servo_settings = pose_bone.bone.servo_settings
    rotation_euler = matrix_visual(pose_bone).to_euler()
    rotation_axis_index = int(servo_settings.rotation_axis)
    rotation_in_degrees = round(math.degrees(
        rotation_euler[rotation_axis_index]) * servo_settings.multiplier, 2)

    if servo_settings.reverse_direction:
        rotation_in_degrees = rotation_in_degrees * -1

    angle = servo_settings.neutral_angle - rotation_in_degrees
    position = round(range_map(angle, 0, servo_settings.rotation_range,
                               servo_settings.position_min, servo_settings.position_max), precision)

    check_min = servo_settings.position_min
    check_max = servo_settings.position_max

    if position < check_min or position > check_max:
        in_range = False
    else:
        in_range = True

    return position, round(angle, 2), in_range


def calculate_positions(context, precision):
    scene = context.scene
    window_manager = context.window_manager
    start = scene.frame_start
    end = scene.frame_end + 1

    positions = {}
    pose_bones = []

    for pose_bone in context.object.pose.bones:
        servo_settings = pose_bone.bone.servo_settings
        if servo_settings.active:
            pose_bones.append(pose_bone)
            positions[servo_settings.servo_id] = []

    if not pose_bones:
        return positions

    # Pre‑cache rest‑pose matrix inverses for all active bones
    cache_bone_matrices(pose_bones)

    if precision == 0:
        precision = None

    window_manager.progress_begin(min=start, max=end)

    for frame in range(start, end):
        scene.frame_set(frame)

        for pose_bone in pose_bones:
            bone = pose_bone.bone
            position, _angle, in_range = calculate_position(pose_bone, precision)

            if not in_range:
                clear_matrix_cache()
                raise RuntimeError(
                    f"Calculated position {position} for bone {bone.name} "
                    + f"is out of range at frame {frame}."
                )

            positions[bone.servo_settings.servo_id].append(position)

        window_manager.progress_update(frame)

    clear_matrix_cache()
    window_manager.progress_end()

    return positions
