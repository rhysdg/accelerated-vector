"""
Vector .bin animation format parser using binary FlatBuffers decoding.

The format uses FlatBuffers with root type CozmoAnim::AnimClips.
Struct layout verified from cozmo_anim_generated.h (WireOS victor repo).

Root type: AnimClips → clips: Vector<Offset<AnimClip>>
  AnimClip → Name: string, keyframes: Offset<Keyframes>
    Keyframes: HeadAngle, LiftHeight, BodyMotion, etc. vectors
      HeadAngle: triggerTime_ms (u32), durationTime_ms (u32), angle_deg (i8), angleVariability_deg (u8)
      LiftHeight: triggerTime_ms (u32), durationTime_ms (u32), height_mm (u8), heightVariability_mm (u8)
"""

import struct
import os
import json


# ── FlatBuffers helpers ───────────────────────────────────────────────────

def _i32(buf, off):
    return struct.unpack_from('<i', buf, off)[0]

def _u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]

def _u16(buf, off):
    return struct.unpack_from('<H', buf, off)[0]

def _i8(buf, off):
    return struct.unpack_from('<b', buf, off)[0]

def _u8(buf, off):
    return struct.unpack_from('<B', buf, off)[0]


def _table_vtable(buf, table_off):
    """Return (vtable_pos, vtable_size, table_size) for a table at table_off."""
    vt_rel = _i32(buf, table_off)
    vt_pos = table_off - vt_rel
    vs = _u16(buf, vt_pos)
    ts = _u16(buf, vt_pos + 2)
    return vt_pos, vs, ts


def _table_field(buf, table_off, vt_num):
    """
    Read a field value from a FlatBuffers table.
    Returns (absolute_position_of_field_data, raw_u32_value).
    If field is not present, returns (None, None).
    """
    vt_pos, vs, ts = _table_vtable(buf, table_off)
    field_idx = (vt_num - 4) // 2
    field_off_bytes = 4 + field_idx * 2
    if field_off_bytes + 2 > vs:
        return None, None
    field_off = _u16(buf, vt_pos + field_off_bytes)
    if field_off == 0:
        return None, None
    abs_pos = table_off + field_off
    val = _u32(buf, abs_pos)
    return abs_pos, val


def _read_string(buf, off):
    """Read a FlatBuffers string (length-prefixed) at offset."""
    if off is None or off >= len(buf) - 4:
        return ''
    length = _u32(buf, off)
    if length <= 0 or off + 4 + length > len(buf):
        return ''
    return buf[off + 4:off + 4 + length].decode('ascii', errors='replace')


def _read_vector(buf, vec_uoffset_pos, vec_uoffset_val):
    """
    Read a FlatBuffers vector of uoffset_t.
    Returns (vector_data_start, vector_length).
    The vector data starts at this position; elements are at vector_data + 4, +8, etc.
    """
    vec_start = vec_uoffset_pos + vec_uoffset_val
    vec_len = _u32(buf, vec_start)
    return vec_start, vec_len


# ── Parsing ────────────────────────────────────────────────────────────────

def parse_headangle_kf(buf, kf_off):
    """Parse a HeadAngleKeyFrame at table position kf_off."""
    _, trigger = _table_field(buf, kf_off, 4)
    _, duration = _table_field(buf, kf_off, 6)
    angle_pos, angle_val = _table_field(buf, kf_off, 8)
    angle = _i8(buf, angle_pos) if angle_pos else 0
    var_pos, _ = _table_field(buf, kf_off, 10)
    variability = _u8(buf, var_pos) if var_pos else 0
    return {
        'triggerTime_ms': trigger or 0,
        'durationTime_ms': duration or 0,
        'angle_deg': angle,
        'type': 'HEAD',
    }


def parse_liftheight_kf(buf, kf_off):
    """Parse a LiftHeightKeyFrame at table position kf_off."""
    _, trigger = _table_field(buf, kf_off, 4)
    _, duration = _table_field(buf, kf_off, 6)
    height_pos, _ = _table_field(buf, kf_off, 8)
    height = _u8(buf, height_pos) if height_pos else 0
    var_pos, _ = _table_field(buf, kf_off, 10)
    variability = _u8(buf, var_pos) if var_pos else 0
    return {
        'triggerTime_ms': trigger or 0,
        'durationTime_ms': duration or 0,
        'height_mm': height,
        'type': 'LIFT',
    }


def parse_body_motion_kf(buf, kf_off):
    """Parse a BodyMotionKeyFrame at table position kf_off."""
    _, trigger = _table_field(buf, kf_off, 4)
    _, duration = _table_field(buf, kf_off, 6)
    radius_pos, radius_val = _table_field(buf, kf_off, 8)
    radius_str = _read_string(buf, radius_val) if radius_pos else ''
    speed_pos, speed_val = _table_field(buf, kf_off, 10)
    speed = struct.unpack_from('<h', buf, speed_pos)[0] if speed_pos else 0
    return {
        'triggerTime_ms': trigger or 0,
        'durationTime_ms': duration or 0,
        'radius_mm': radius_str,
        'speed': speed,
        'type': 'BODY',
    }


def parse_animclip(buf, clip_off):
    """Parse an AnimClip table. Returns {name, keyframes_by_type}."""
    kf_pos, kf_val = _table_field(buf, clip_off, 6)
    name_pos, name_val = _table_field(buf, clip_off, 4)
    name = ''
    if name_pos is not None:
        # name_val is the uoffset from name_pos to the string data
        name_str_abs = name_pos + name_val
        name_len = _u32(buf, name_str_abs)
        if name_len > 0 and name_len < 100:
            try:
                name = buf[name_str_abs + 4:name_str_abs + 4 + name_len].decode('ascii', errors='replace')
            except:
                pass
    
    keyframes = {'HEAD': [], 'LIFT': [], 'BODY': [], 'EVENT': [], 'FACE': [], 'OTHER': []}
    
    if kf_pos:
        kf_off = kf_pos + kf_val
        kf_vt_pos, kf_vs, kf_ts = _table_vtable(buf, kf_off)
        
        # Scan all fields in the Keyframes table — each is a vector of a keyframe type
        num_fields = (kf_vs - 4) // 2
        for field_idx in range(num_fields):
            field_vt = 4 + field_idx * 2
            field_off = _u16(buf, kf_vt_pos + 4 + field_idx * 2)
            if field_off == 0:
                continue
            
            field_abs = kf_off + field_off
            field_val = _u32(buf, field_abs)
            vec_start, vec_len = _read_vector(buf, field_abs, field_val)
            
            if vec_len == 0:
                continue
            
            # Try to parse the first keyframe to determine the type
            first_elem_off = _u32(buf, vec_start + 4) + vec_start + 4
            
            # Determine type by trying each parser
            for parse_fn, motor_type in [
                (parse_headangle_kf, 'HEAD'),
                (parse_liftheight_kf, 'LIFT'),
                (parse_body_motion_kf, 'BODY'),
            ]:
                try:
                    sample = parse_fn(buf, first_elem_off)
                    if sample.get('durationTime_ms', 0) > 0 or sample.get('triggerTime_ms', 0) > 0:
                        # Parse all keyframes of this type
                        for elem_idx in range(vec_len):
                            elem_off = _u32(buf, vec_start + 4 + elem_idx * 4) + vec_start + 4
                            kf = parse_fn(buf, elem_off)
                            keyframes[motor_type].append(kf)
                        break
                except:
                    pass
    
    return {'name': name, 'keyframes': keyframes}


def parse_bin_file(filepath):
    """Parse a .bin file and return animation data with all clips and keyframes."""
    with open(filepath, 'rb') as f:
        buf = f.read()
    
    # Root uoffset at byte 0
    root_off = _u32(buf, 0)
    
    # Root should be AnimClips with clips vector at VT=4
    clips_vec_pos, clips_vec_val = _table_field(buf, root_off, 4)
    if clips_vec_pos is None:
        # Try offset 0 (no root uoffset)
        clips_vec_pos, clips_vec_val = _table_field(buf, 0, 4)
        if clips_vec_pos is None:
            return {'name': os.path.basename(filepath), 'clips': []}
        vec_start, vec_len = _read_vector(buf, clips_vec_pos, clips_vec_val)
    else:
        vec_start, vec_len = _read_vector(buf, clips_vec_pos, clips_vec_val)
    
    clips = []
    for clip_idx in range(vec_len):
        clip_uoffset_abs = vec_start + 4 + clip_idx * 4
        clip_uoffset_val = _u32(buf, clip_uoffset_abs)
        clip_off = clip_uoffset_abs + clip_uoffset_val
        try:
            clip = parse_animclip(buf, clip_off)
            clips.append(clip)
        except Exception as e:
            pass
    
    return {'name': os.path.basename(filepath), 'clips': clips}


# ── Blender conversion ────────────────────────────────────────────────────

def extract_blender_keyframes(parsed_data):
    """Convert parsed animation data to Blender keyframe format."""
    kf_by_motor = {'HEAD': [], 'LIFT': [], 'BODY': []}
    
    for clip in parsed_data.get('clips', []):
        for motor_type, kfs in clip['keyframes'].items():
            for kf in kfs:
                entry = {'name': clip['name'], 'motor': motor_type}
                if motor_type == 'HEAD':
                    entry['angle_deg'] = kf['angle_deg']
                    entry['duration_ms'] = kf['durationTime_ms']
                    entry['trigger_ms'] = kf['triggerTime_ms']
                elif motor_type == 'LIFT':
                    entry['height_mm'] = kf['height_mm']
                    entry['duration_ms'] = kf['durationTime_ms']
                    entry['trigger_ms'] = kf['triggerTime_ms']
                elif motor_type == 'BODY':
                    entry['speed'] = kf['speed']
                    entry['duration_ms'] = kf['durationTime_ms']
                    entry['trigger_ms'] = kf['triggerTime_ms']
                kf_by_motor[motor_type].append(entry)
    
    return kf_by_motor


def parse_anim_manifest(manifest_path):
    """Parse the anim_manifest.json and return name→duration map."""
    try:
        with open(manifest_path) as f:
            entries = json.load(f)
        return {e['name']: e['length_ms'] for e in entries}
    except:
        return {}
