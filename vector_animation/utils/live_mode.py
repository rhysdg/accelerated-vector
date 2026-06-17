# pylint: disable=import-outside-toplevel, broad-exception-caught

import math
import bpy

from ..utils.servo_settings import get_active_pose_bones, convert_vector_range
from ..utils.converter import calculate_position, cache_bone_matrices

class LiveMode:
    COMMAND_START = 0x3C
    COMMAND_END = 0x3E

    METHOD_SERIAL = "SERIAL"
    METHOD_SOCKET = "SOCKET"
    METHOD_VECTOR = "VECTOR"

    STEP_DURATION_BASE = .3

    _last_positions = {}
    _connection = None
    _handler_enabled = True
    _cached_pose_bones = None

    # --- Position jump state (used by timer-based jump) ---
    _jump_active = False
    _jump_targets = []
    _jump_abs_steps = 0
    _jump_current_step = 0

    @classmethod
    def cache_pose_bones(cls):
        """Pre‑cache the list of active pose bones and their matrix inverses."""
        bones = get_active_pose_bones(bpy.context.scene)
        cache_bone_matrices(bones)
        cls._cached_pose_bones = bones

    @classmethod
    def clear_cache(cls):
        cls._cached_pose_bones = None

    @classmethod
    def set_connection(cls, connection):
        cls._connection = connection

    @classmethod
    def is_connected(cls):
        import serial
        import websocket

        method = bpy.context.window_manager.servo_animation.live_mode_method

        if method == LiveMode.METHOD_SERIAL:
            return (
                isinstance(cls._connection, serial.Serial)
                and cls._connection.is_open
                and (
                    cls._connection.port in cls.get_serial_ports()
                    or bpy.app.background
                )
            )

        if method == LiveMode.METHOD_SOCKET:
            return (
                isinstance(cls._connection, websocket.WebSocket)
                and cls._connection.connected
            )

        if method == LiveMode.METHOD_VECTOR:
            import anki_vector
            return (
                isinstance(cls._connection, anki_vector.Robot)
                and cls._connection.conn._thread
            )

        return False

    @classmethod
    def is_handler_enabled(cls):
        return cls._handler_enabled

    @classmethod
    def enable_handler(cls):
        cls._handler_enabled = True

    @classmethod
    def disable_handler(cls):
        cls._handler_enabled = False

    @classmethod
    def get_serial_ports(cls):
        from serial.tools import list_ports
        return [port.device for port in list_ports.comports()]

    @classmethod
    def get_last_position(cls, servo_id):
        return cls._last_positions.get(servo_id)

    @classmethod
    def handler(cls, _scene, _depsgraph):
        """Frame‑change handler — fires once per frame via frame_change_post."""
        if not cls.is_handler_enabled():
            return
        # If a position jump is in progress, skip frame processing
        if cls._jump_active:
            return

        cls.disable_handler()

        threshold_exceeded = False
        target_positions = []
        servo_animation = bpy.context.window_manager.servo_animation
        pose_bones = cls._cached_pose_bones or get_active_pose_bones(bpy.context.scene)

        for pose_bone in pose_bones:
            position, _angle, in_range = calculate_position(pose_bone, None)

            if not in_range:
                continue

            servo_settings = pose_bone.bone.servo_settings
            servo_id = servo_settings.servo_id
            step = round(servo_settings.threshold / 10)
            target_positions.append((servo_id, position, step))

            if (
                servo_id in cls._last_positions
                and abs(position - cls._last_positions[servo_id]) > servo_settings.threshold
            ):
                threshold_exceeded = True

        if servo_animation.position_jump_handling and threshold_exceeded:
            cls._start_position_jump(target_positions)
        else:
            cls.handle_default(target_positions)

        cls.enable_handler()

    @classmethod
    def handle_default(cls, target_positions):
        for servo_id, position, _step in target_positions:
            cls.send_position(servo_id, position)

    # --- Timer-based position jump (non-blocking) ---

    @classmethod
    def _start_position_jump(cls, target_positions):
        """Begin a stepped position jump via a Blender timer (non‑blocking)."""
        if bpy.context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)

        cls._jump_active = True
        cls._jump_targets = target_positions
        cls._jump_current_step = 0

        # Calculate total steps needed (max across all servos)
        abs_steps = 0
        for servo_id, position, step in target_positions:
            diff = abs(position - cls._last_positions.get(servo_id, position))
            steps = math.ceil(diff / step) if step > 0 else 1
            if steps > abs_steps:
                abs_steps = steps
        cls._jump_abs_steps = abs_steps

        window_manager = bpy.context.window_manager
        window_manager.progress_begin(0, abs_steps)

        bpy.app.timers.register(cls._jump_timer, first_interval=0.01)

    @classmethod
    def _jump_timer(cls):
        """Timer callback — advance one step of the position jump."""
        if cls._jump_current_step >= cls._jump_abs_steps:
            cls._finish_position_jump()
            return None  # stop the timer

        window_manager = bpy.context.window_manager
        window_manager.progress_update(cls._jump_current_step)

        for servo_id, position, step in cls._jump_targets:
            new_position = cls._last_positions.get(servo_id)

            if position == new_position:
                continue

            if position > new_position:
                new_position += step
            else:
                new_position -= step

            if abs(position - new_position) < step:
                new_position = position

            cls.send_position(servo_id, new_position)

        cls._jump_current_step += 1
        return 0.01  # call again after 10 ms

    @classmethod
    def _finish_position_jump(cls):
        """Clean up after the jump completes."""
        window_manager = bpy.context.window_manager
        window_manager.progress_end()

        cls._jump_active = False
        cls._jump_targets = []
        cls._jump_abs_steps = 0
        cls._jump_current_step = 0

    @classmethod
    def send_position(cls, servo_id, position):
        if position == cls._last_positions.get(servo_id):
            return

        command = [cls.COMMAND_START, servo_id]
        command += position.to_bytes(2, 'big')
        command += [cls.COMMAND_END]

        servo_animation = bpy.context.window_manager.servo_animation

        try:
            if servo_animation.live_mode_method == LiveMode.METHOD_SERIAL:
                cls._connection.write(command)
            elif servo_animation.live_mode_method == LiveMode.METHOD_SOCKET:
                cls._connection.send_binary(bytes(command))
            elif servo_animation.live_mode_method == LiveMode.METHOD_VECTOR:
                # Convert the raw servo position (0-1000 range) to Vector's 0-1 lift range
                lift_value = convert_vector_range(
                    position,
                    old_min=0, old_max=1000,
                    new_min=0, new_max=1
                )
                cls._connection.behavior.set_lift_height(lift_value)

            cls._last_positions[servo_id] = position
        except Exception:
            bpy.ops.servo_animation.stop_live_mode(unexpected=True)

    @classmethod
    def close_connection(cls):
        cls.clear_cache()
        cls._last_positions = {}
        method = bpy.context.window_manager.servo_animation.live_mode_method

        if cls._connection:
            if method == LiveMode.METHOD_VECTOR:
                cls._connection.behavior.say_text("bye bye!")
                cls._connection.disconnect()
            else:
                cls._connection.close()
