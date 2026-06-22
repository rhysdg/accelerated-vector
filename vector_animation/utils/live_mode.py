# pylint: disable=import-outside-toplevel, broad-exception-caught

import math
import threading
import bpy

from ..utils.servo_settings import get_active_pose_bones
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

    # Servo → (motor_type, pos_min, pos_max) lookup for Vector SDK dispatch.
    _servo_info = {}
    # Armature rest matrices (name → matrix_world) for Object‑Mode delta
    _armature_rest = {}

    # --- Async I/O: producer (handler) / consumer (worker thread) ---
    # The frame_change handler only updates _pending and signals the worker,
    # so Blender's playback is never blocked by network latency.
    _send_lock = threading.Lock()
    _send_event = threading.Event()
    _pending = {}          # servo_id → position  (latest per servo)
    _worker_thread = None
    _worker_stop = False
    _connection_error = None
    # Cached on the main thread; read by the worker (bpy.context is main-only).
    _method = None
    # Skip the first handler call after registration — it fires on the
    # current frame which may override the connect-time neutral position.
    _handler_skip_first = False

   # Auto-calibration for Vector mode: {motor_type: {axis, min_deg, max_deg, armature}}
    # Keyed by Vector motor type (LIFT/HEAD/...) rather than servo_id, because
    # servo_id is not unique on a multi-armature rig and collisions would
    # bind a motor to the wrong armature.  Each Vector motor maps to exactly
    # one bone (the bone whose `vector_motor` is set to that type).
    _vector_calibration = {}

    # Per-motor last-dispatch timestamps (time.monotonic) to throttle
    # Vector SDK action cancel/restart cycles during animation playback.
    # Without this, 30fps playback floods the SDK with cancel+restart calls
    # that cause hard jumps instead of smooth motion.
    _motor_last_dispatch = {}
    # Minimum seconds between dispatches per motor (Vector mode).
    # 0.15 s = ~6.7 Hz per motor — enough for smooth motion without
    # flooding the action queue.
    _MOTOR_DISPATCH_MIN_DELTA = 0.15

    @classmethod
    def cache_pose_bones(cls):
        """Pre‑cache the list of active pose bones, matrix inverses,
        servo information, and armature rest matrices."""
        bones = get_active_pose_bones(bpy.context.scene)
        cache_bone_matrices(bones)
        cls._cached_pose_bones = bones

        # Build servo_info lookup from bone config
        cls._servo_info.clear()
        for pbone in bones:
            s = pbone.bone.servo_settings
            cls._servo_info[s.servo_id] = (
                s.vector_motor,
                s.position_min,
                s.position_max,
            )

        # Save each armature's rest-pose world matrix (at frame_start = neutral)
        scene = bpy.context.scene
        original_frame = scene.frame_current
        scene.frame_set(scene.frame_start)
        bpy.context.view_layer.update()

        cls._armature_rest.clear()
        for pbone in bones:
            arm = pbone.id_data
            if arm.name not in cls._armature_rest:
                cls._armature_rest[arm.name] = arm.matrix_world.copy()

        # For Vector mode, auto-calibrate by scanning the animation timeline
        method = bpy.context.window_manager.servo_animation.live_mode_method
        if method == cls.METHOD_VECTOR:
            cls.calibrate_vector_bones(bones)

        # Always restore the original frame
        scene.frame_set(original_frame)
        bpy.context.view_layer.update()

    # Only these motor types are animated — other motors (BODY, wheels, etc.)
    # are skipped until the rest of the rig is figured out.
    _ACTIVE_MOTORS = frozenset(("LIFT", "HEAD"))

    @classmethod
    def calibrate_vector_bones(cls, bones):
        """Scan the animation timeline to auto-detect each motor's rotation
        axis and range.  Eliminates all manual servo config for Vector mode.

        Calibration is keyed by Vector motor type (LIFT, HEAD only), NOT by
        servo_id — servo_id is not unique on a multi-armature rig (unconfigured
        pivot bones all default to 0) and collisions would bind a motor to the
        wrong armature.  For each motor type we find the single bone whose
        ``vector_motor`` matches and calibrate that bone's own armature.

        Motors not in ``_ACTIVE_MOTORS`` (e.g. BODY, LEFT_WHEEL, RIGHT_WHEEL,
        BODY_TURN) are skipped.
        """
        scene = bpy.context.scene
        start = scene.frame_start
        end = scene.frame_end
        cls._vector_calibration.clear()

        # Map each active motor type → list of pose bones that claim it.
        # On a multi-armature rig (e.g. Vector's lift) several pivot bones may
        # share the default motor type.  We scan all of them and keep the one
        # with the largest animation span as the "driver" for that motor.
        motor_to_bones = {}
        for pbone in bones:
            motor = pbone.bone.servo_settings.vector_motor
            if motor not in cls._ACTIVE_MOTORS:
                continue
            motor_to_bones.setdefault(motor, []).append(pbone)

        if not motor_to_bones:
            print(f"[LiveMode] calibrate: no bones with active vector_motor "
                  f"({_ACTIVE_MOTORS}) — set Vector Motor to LIFT or HEAD")
            return

        print(f"[LiveMode] calibrate: active motors={cls._ACTIVE_MOTORS}")

        # Per-(motor, bone) min/max angle accumulators on all 3 axes.
        # raw[motor][bone_name] = {'mins':[x,y,z], 'maxs':[x,y,z], 'armature':, 'bone':}
        raw = {}
        for motor, pbones in motor_to_bones.items():
            raw[motor] = {}
            for pbone in pbones:
                raw[motor][pbone.name] = {
                    'armature': pbone.id_data.name,
                    'bone': pbone.name,
                    'pbone': pbone,
                    'mins': [float('inf')] * 3,
                    'maxs': [float('-inf')] * 3,
                }

        for frame in range(start, end + 1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()

            for motor, pbones in motor_to_bones.items():
                for pbone in pbones:
                    arm_obj = pbone.id_data
                    arm_rest = cls._armature_rest.get(arm_obj.name)
                    if arm_rest is None:
                        continue

                    # Compute the bone's world-space rotation delta from rest,
                    # which captures both object-level armature animation
                    # (e.g. Lift armature rotates in world) AND bone-level pose
                    # animation (e.g. Head bone rotates in pose mode).
                    bone_rest_world = arm_rest @ pbone.bone.matrix_local
                    bone_current_world = arm_obj.matrix_world @ pbone.matrix
                    delta_mat = bone_current_world @ bone_rest_world.inverted()
                    euler = delta_mat.to_euler()

                    entry = raw[motor][pbone.name]
                    for axis in range(3):
                        deg = math.degrees(euler[axis])
                        if deg < entry['mins'][axis]:
                            entry['mins'][axis] = deg
                        if deg > entry['maxs'][axis]:
                            entry['maxs'][axis] = deg

        # For each motor, pick the bone with the largest span as the driver.
        for motor, entries in raw.items():
            best_entry = None
            best_span = -1.0
            best_axis = 0
            for bone_name, entry in entries.items():
                for axis in range(3):
                    span = entry['maxs'][axis] - entry['mins'][axis]
                    if span > best_span:
                        best_span = span
                        best_axis = axis
                        best_entry = entry

            if best_entry is None or best_span < 0.5:
                arms = [e['armature'] for e in entries.values()]
                print(f"[LiveMode] calibrate: motor={motor} "
                      f"armatures={arms} — NO ANIMATION (span {best_span:.2f}°), skipping")
                continue

            min_deg = best_entry['mins'][best_axis]
            max_deg = best_entry['maxs'][best_axis]

            cls._vector_calibration[motor] = {
                'axis': best_axis,
                'min_deg': min_deg,
                'max_deg': max_deg,
                'armature': best_entry['armature'],
                'bone': best_entry['bone'],
                'pbone': best_entry['pbone'],
            }
            print(f"[LiveMode] calibrate: motor={motor} "
                  f"armature={best_entry['armature']} bone={best_entry['bone']} "
                  f"axis={best_axis} range=[{min_deg:.1f}, {max_deg:.1f}]° "
                  f"span={best_span:.1f}°")

        # Log which motors were bound (only LIFT and HEAD should appear)
        bound = list(cls._vector_calibration.keys())
        skipped = [m for m in cls._ACTIVE_MOTORS if m not in cls._vector_calibration]
        if skipped:
            print(f"[LiveMode] calibrate: active motors bound={bound}, "
                  f"skipped (no animation)={skipped}")
        else:
            print(f"[LiveMode] calibrate: bound motors={bound}")

    @classmethod
    def clear_cache(cls):
        cls._cached_pose_bones = None
        cls._servo_info.clear()
        cls._armature_rest.clear()
        cls._vector_calibration.clear()
        cls._motor_last_dispatch.clear()
        cls._handler_skip_first = False
        with cls._send_lock:
            cls._pending.clear()

    @classmethod
    def set_connection(cls, connection):
        cls._connection = connection

    # ── Worker thread (consumer) ──────────────────────────────────────────

    @classmethod
    def start_worker(cls):
        """Start the background I/O thread. Safe to call repeatedly."""
        if cls._worker_thread is not None and cls._worker_thread.is_alive():
            return
        # Cache the live-mode method here (main thread) — the worker cannot
        # safely touch bpy.context.
        cls._method = bpy.context.window_manager.servo_animation.live_mode_method
        cls._worker_stop = False
        cls._send_event.clear()
        with cls._send_lock:
            cls._pending.clear()
        cls._handler_skip_first = True
        cls._worker_thread = threading.Thread(
            target=cls._worker_loop, name="LiveModeIO", daemon=True,
        )
        cls._worker_thread.start()
        print(f"[LiveMode] worker started (method={cls._method})")

    @classmethod
    def stop_worker(cls):
        """Signal the worker to stop and wait for it to finish."""
        cls._worker_stop = True
        cls._send_event.set()  # wake it up
        thread = cls._worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        cls._worker_thread = None
        with cls._send_lock:
            cls._pending.clear()
        cls._send_event.clear()

    @classmethod
    def _worker_loop(cls):
        """Drain the latest pending positions and dispatch to the robot."""
        print("[LiveMode] worker loop entered")
        import time as _time
        while not cls._worker_stop:
            # Wait for new data (or stop signal), with a timeout so we can
            # still check _worker_stop if no frames are produced.
            cls._send_event.wait(timeout=0.05)
            cls._send_event.clear()
            if cls._worker_stop:
                print("[LiveMode] worker stopping")
                break

            # Snapshot the latest per-servo targets under the lock.
            with cls._send_lock:
                if not cls._pending:
                    continue
                snapshot = cls._pending.copy()
                cls._pending.clear()
            print(f"[LiveMode] worker woke, dispatching {len(snapshot)} servo(s)")

            _now = _time.monotonic()
            for servo_id, position in snapshot.items():
                if cls._worker_stop:
                    break

                # Per-motor throttle: skip dispatch if not enough time has passed
                # since the last dispatch for this motor.  This prevents the
                # Vector SDK from canceling and restarting actions too fast,
                # which causes hard jumps during animation playback.
                if isinstance(servo_id, str) and servo_id.startswith("motor:"):
                    last = cls._motor_last_dispatch.get(servo_id, 0)
                    if _now - last < cls._MOTOR_DISPATCH_MIN_DELTA:
                        continue

                try:
                    cls._dispatch(servo_id, position)
                    # Record dispatch time for throttle
                    if isinstance(servo_id, str) and servo_id.startswith("motor:"):
                        cls._motor_last_dispatch[servo_id] = _now
                    print(f"[LiveMode] dispatched servo={servo_id} pos={position}")
                except Exception as exc:  # pylint: disable=broad-except
                    import traceback
                    traceback.print_exc()
                    cls._connection_error = exc
                    cls._schedule_stop_on_main()
                    return

            # Rate-limit: cap dispatches at ~20 Hz.  Vector's motors can't
            # react faster than this, and flooding the action queue with
            # cancellable actions causes gRPC errors after a few seconds.
            _time.sleep(0.05)

    @classmethod
    def _schedule_stop_on_main(cls):
        """bpy.ops must run on the main thread — defer via a one-shot timer."""
        def _stop():
            try:
                bpy.ops.servo_animation.stop_live_mode(unexpected=True)
            except Exception:  # pylint: disable=broad-except
                pass
            return None  # one-shot
        try:
            bpy.app.timers.register(_stop, first_interval=0.0)
        except Exception:  # pylint: disable=broad-except
            pass

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
        """Frame‑change handler (producer).

        Computes target positions and hands them to the worker thread.
        No network I/O happens here, so Blender playback stays responsive.
        """
        if not cls.is_handler_enabled():
            return
        # If a position jump is in progress, skip frame processing
        if cls._jump_active:
            return
        # Skip the first handler call after registration — it fires on the
        # current frame which may override the connect-time neutral position.
        if cls._handler_skip_first:
            cls._handler_skip_first = False
            return

        servo_animation = bpy.context.window_manager.servo_animation
        use_vector_cal = (
            servo_animation.live_mode_method == LiveMode.METHOD_VECTOR
            and bool(cls._vector_calibration)
        )
        print(f"[LiveMode] handler fire, "
              f"worker_alive={cls._worker_thread is not None and cls._worker_thread.is_alive()}, "
              f"vector_cal={use_vector_cal} motors={list(cls._vector_calibration.keys()) if use_vector_cal else []}")

        target_positions = []
        threshold_exceeded = False

        if use_vector_cal:
            # ── Vector path: iterate calibrated motors, one command each ──
            for motor, cal in cls._vector_calibration.items():
                pbone = cal.get('pbone')
                if pbone is None:
                    continue
                arm_obj = pbone.id_data
                arm_rest = cls._armature_rest.get(arm_obj.name)
                if arm_rest is None:
                    continue

                # Compute the bone's world-space rotation delta from rest,
                # which captures both object-level armature animation
                # (e.g. Lift armature rotates in world) AND bone-level pose
                # animation (e.g. Head bone rotates in pose mode).
                bone_rest_world = arm_rest @ pbone.bone.matrix_local
                bone_current_world = arm_obj.matrix_world @ pbone.matrix
                delta_mat = bone_current_world @ bone_rest_world.inverted()
                euler = delta_mat.to_euler()
                deg = math.degrees(euler[cal['axis']])

                span = cal['max_deg'] - cal['min_deg']
                norm = (deg - cal['min_deg']) / span if span > 0.01 else 0.5
                norm = max(0.0, min(1.0, norm))
                position = round(norm * 1000)

                # Key by motor type so dispatch sends exactly one command
                # per Vector motor per frame.
                key = f"motor:{motor}"
                step = 1
                target_positions.append((key, position, step))
                last = cls._last_positions.get(key)
                print(f"[LiveMode]   motor={motor} armature={arm_obj.name} "
                      f"deg={deg:.1f} norm={norm:.2f} pos={position} last={last}")
                if last is not None and abs(position - last) > 5:
                    threshold_exceeded = True
        else:
            # ── Legacy servo-config path (serial / socket) ──
            pose_bones = cls._cached_pose_bones or get_active_pose_bones(bpy.context.scene)
            print(f"[LiveMode] handler fire, bones={len(pose_bones) if pose_bones else 0}")
            for pose_bone in pose_bones:
                arm_obj = pose_bone.id_data
                arm_rest = cls._armature_rest.get(arm_obj.name)
                servo_settings = pose_bone.bone.servo_settings
                servo_id = servo_settings.servo_id

                position, _angle, in_range = calculate_position(
                    pose_bone, None,
                    armature_world=arm_obj.matrix_world,
                    rest_world=arm_rest,
                )

                if not in_range:
                    print(f"[LiveMode]   {pose_bone.name}: OUT OF RANGE pos={position} angle={_angle:.1f}")
                    continue

                step = round(servo_settings.threshold / 10)
                target_positions.append((servo_id, position, step))
                print(f"[LiveMode]   {pose_bone.name}: id={servo_id} pos={position} last={cls._last_positions.get(servo_id)}")

                if (
                    servo_id in cls._last_positions
                    and abs(position - cls._last_positions.get(servo_id)) > servo_settings.threshold
                ):
                    threshold_exceeded = True

        # Position jump handling stops the animation and moves servos in
        # sub‑steps via a timer.  For the Vector SDK this is counter‑productive
        # (Vector has built‑in smooth motion) and actively harmful (it cancels
        # playback).  Skip it entirely for Vector method.
        use_jump = (
            servo_animation.live_mode_method != LiveMode.METHOD_VECTOR
            and servo_animation.position_jump_handling
            and threshold_exceeded
        )
        if use_jump:
            cls._start_position_jump(target_positions)
        else:
            cls.handle_default(target_positions)

    @classmethod
    def handle_default(cls, target_positions):
        """Enqueue the latest target positions for the worker thread."""
        if not target_positions:
            print("[LiveMode] handle_default: no targets")
            return
        with cls._send_lock:
            changed = False
            for servo_id, position, _step in target_positions:
                if position != cls._last_positions.get(servo_id):
                    cls._pending[servo_id] = position
                    changed = True
        print(f"[LiveMode] handle_default: {len(target_positions)} targets, changed={changed}, pending={len(cls._pending)}")
        if changed:
            cls._send_event.set()

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

    # ── Vector SDK range helpers ──────────────────────────────────────────

    @classmethod
    def _range_map(cls, value, old_min, old_max, new_min, new_max):
        """Linearly re‑map *value* from [old_min, old_max] → [new_min, new_max]."""
        if old_max == old_min:
            return (new_min + new_max) / 2.0
        return (value - old_min) * (new_max - new_min) / (old_max - old_min) + new_min

    @classmethod
    def _send_vector_pos(cls, key, position, motor_type, pos_min, pos_max):
        """Dispatch a single position value to the correct Vector SDK call.

        *key* is the caller's identity for this command (a ``"motor:<TYPE>"``
        string in the calibrated path) and is used to record the last sent
        position on success.

        Returns True on success, False on failure.

        All motion calls use ``_return_future=True`` so they return
        immediately (fire‑and‑forget) instead of blocking until the physical
        motion completes.
        """
        import anki_vector.util as vector_util

        conn = cls._connection
        behavior = conn.behavior
        motors = conn.motors
        norm = cls._range_map(position, pos_min, pos_max, 0.0, 1.0)

        try:
            if motor_type == 'LIFT':
                behavior.set_lift_height(norm, _return_future=True)

            elif motor_type == 'HEAD':
                angle_deg = cls._range_map(norm, 0.0, 1.0, -45.0, 45.0)
                behavior.set_head_angle(vector_util.Angle(degrees=angle_deg), _return_future=True)

            elif motor_type == 'LEFT_WHEEL':
                speed = cls._range_map(norm, 0.0, 1.0, -500.0, 500.0)
                right_speed = cls._last_positions.get('motor:RIGHT_WHEEL', 0.0)
                motors.set_wheel_motors(
                    round(speed), round(right_speed),
                    round(speed * 0.5), round(right_speed * 0.5),
                    _return_future=True,
                )

            elif motor_type == 'RIGHT_WHEEL':
                speed = cls._range_map(norm, 0.0, 1.0, -500.0, 500.0)
                left_speed = cls._last_positions.get('motor:LEFT_WHEEL', 0.0)
                motors.set_wheel_motors(
                    round(left_speed), round(speed),
                    round(left_speed * 0.5), round(speed * 0.5),
                    _return_future=True,
                )

            elif motor_type == 'BODY_TURN':
                angle_deg = cls._range_map(norm, 0.0, 1.0, -180.0, 180.0)
                behavior.turn_in_place(vector_util.Angle(degrees=angle_deg), _return_future=True)

            cls._last_positions[key] = position
            return True

        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[Vector] key={key} motor={motor_type}: {exc}")
            return False

    # ── Enqueue (producer-side, called from main thread) ─────────────────

    @classmethod
    def send_position(cls, servo_id, position):
        """Non-blocking: stash the latest position for *servo_id* and wake
        the worker thread.  Used by the jump timer."""
        if position == cls._last_positions.get(servo_id):
            return
        with cls._send_lock:
            cls._pending[servo_id] = position
        cls._send_event.set()

    # ── Dispatch (consumer-side, called from worker thread) ──────────────

    @classmethod
    def _dispatch(cls, key, position):
        """Perform the actual I/O for one target.  Runs on the worker thread.

        *key* is either an integer servo_id (legacy serial/socket mode) or a
        string of the form ``"motor:LIFT"`` (Vector mode, keyed by motor type).

        Returns True on success, False on failure.  Only updates
        ``_last_positions`` on success so that failed sends are retried
        on the next frame rather than silently swallowed.
        """
        method = cls._method

        # Vector mode: key is "motor:<TYPE>"
        if isinstance(key, str) and key.startswith("motor:"):
            motor_type = key.split(":", 1)[1]
            return cls._send_vector_pos(key, position, motor_type, 0, 1000)

        # Legacy serial / socket path: key is an integer servo_id
        servo_id = key
        command = [cls.COMMAND_START, servo_id]
        command += position.to_bytes(2, 'big')
        command += [cls.COMMAND_END]

        try:
            if method == LiveMode.METHOD_SERIAL:
                cls._connection.write(command)
            elif method == LiveMode.METHOD_SOCKET:
                cls._connection.send_binary(bytes(command))
            cls._last_positions[servo_id] = position
            return True
        except Exception:
            return False

    @classmethod
    def close_connection(cls):
        cls.stop_worker()
        cls.clear_cache()
        cls._last_positions = {}
        cls._connection_error = None
        method = bpy.context.window_manager.servo_animation.live_mode_method

        if cls._connection:
            if method == LiveMode.METHOD_VECTOR:
                mute = bpy.context.window_manager.servo_animation.mute_on_connect
                if not mute:
                    cls._connection.behavior.say_text("bye bye!")
                cls._connection.disconnect()
            else:
                cls._connection.close()
