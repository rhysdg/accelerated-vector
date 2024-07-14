def get_active_pose_bones(scene):
    pose_bones = []

    for obj in scene.objects:
        if obj.type != "ARMATURE":
            continue

        for pose_bone in obj.pose.bones:
            if pose_bone.bone.servo_settings.active:
                pose_bones.append(pose_bone)

    return pose_bones


def get_pose_bone_by_servo_id(servo_id, scene):
    for pose_bone in get_active_pose_bones(scene):
        if pose_bone.bone.servo_settings.servo_id == servo_id:
            return pose_bone

    return None


def range_limit_value(value, min_value, max_value):
    if min_value is not None and value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


def has_unique_servo_id(bone, scene):
    for pose_bone in get_active_pose_bones(scene):
        if pose_bone.bone.name == bone.name:
            continue
        if pose_bone.bone.servo_settings.servo_id == bone.servo_settings.servo_id:
            return False

    return True
    

def convert_vector_range(value, old_min=45, old_max=90, new_min=0, new_max=1):
  """
  Blender armature is converted to degrees
  This function itercepts and converts to a 0-1 
  range required for Vecor's lift

  Args:
      value: The value to be scaled.
      old_min: The minimum value in the old range (default 45).
      old_max: The maximum value in the old range (default 90).
      new_min: The minimum value in the new range (default 0).
      new_max: The maximum value in the new range (default 1).

  Returns:
      The scaled value within the new range.
  """

  if old_min >= old_max:
    raise ValueError("old_min must be less than old_max")

  if new_min >= new_max:
    raise ValueError("new_min must be less than new_max")

  old_range = old_max - old_min
  new_range = new_max - new_min

  relative_position = (value - old_min) / old_range
  scaled_value = relative_position * new_range + new_min

  return scaled_value

