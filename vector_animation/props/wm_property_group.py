import bpy

from bpy.types import PropertyGroup
from ..ops.start_live_mode import StartLiveMode
from ..utils.live_mode import LiveMode


def _get_anim_items(self, context):
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
        items.append(("NONE", "No animations — click Refresh List", ""))
    return items


def get_serial_port_items(_self, _context):
    items = []
    ports = LiveMode.get_serial_ports()

    if len(ports) < 1:
        items.append(("NONE", "No port available", ""))
    else:
        for port in ports:
            items.append((port, port, ""))

    return items


class WindowManagerPropertyGroup(PropertyGroup):
    live_mode_method: bpy.props.EnumProperty(
        name="Method",
        items=StartLiveMode.METHOD_ITEMS
    )
    serial_port: bpy.props.EnumProperty(
        name="Port",
        items=get_serial_port_items
    )
    serial_baud: bpy.props.EnumProperty(
        name="Baud Rate",
        default="115200",
        items=[
            ("19200", "19200", ""),
            ("115200", "115200", ""),
            ("192500", "192500", "")
        ]
    )
    socket_host: bpy.props.StringProperty(
        name="Host",
        default="127.0.0.1"
    )
    socket_port: bpy.props.IntProperty(
        name="Port",
        min=0,
        max=65535,
        default=80
    )
    socket_path: bpy.props.StringProperty(
        name="Path",
        default="/"
    )
    robot_ip: bpy.props.StringProperty(
        name="Wire-pod IP",
        description="IP address of the Wire-pod server (not the robot)",
        default="192.168.0.190"
    )
    position_jump_handling: bpy.props.BoolProperty(
        name="Position Jump Handling",
        description=(
            "Slowly move the servos to their new position "
            "when the position difference exceeds the threshold "
            "(disable for Vector SDK — Vector handles smooth motion)"
        ),
        default=False
    )
    mute_on_connect: bpy.props.BoolProperty(
        name="Mute on Connect",
        description=(
            "Suppress voice notifications from the robot on "
            "connect ('Ready to animate!') and disconnect ('bye bye!')"
        ),
        default=False
    )
    robot_anim_list_json: bpy.props.StringProperty(
        name="Animation List (JSON)",
        description="Internal storage for fetched animation list",
        default="[]"
    )
    robot_anim_count: bpy.props.IntProperty(
        name="Animation Count",
        default=0
    )
    robot_anim_select: bpy.props.EnumProperty(
        name="Import Animation",
        description="Select a .bin animation from the robot to import as keyframes",
        items=_get_anim_items
    )
    
