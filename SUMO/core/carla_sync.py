"""
CARLA-SUMO synchronization loop. Used by run_simulation.py for --mode carla.

Traffic control behaviour:
  Single-intersection (default / backward-compat):
    core.traffic_control.step(tls_id, step, emission_dir)

  Network-wide (Carbon-Emission-Traffic, control_scope == "all"):
    A shared NetworkController is created once before the loop, then
    core.traffic_control.step_network(controller, step, ...) is called each
    tick.  SUMO must be the traffic-light authority (--tls-manager sumo).
"""

import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

from .sumo_integration.bridge_helper import BridgeHelper
from .sumo_integration.carla_simulation import CarlaSimulation
from .sumo_integration.constants import INVALID_ACTOR_ID
from .sumo_integration.sumo_simulation import SumoSimulation
from . import traffic_control as core_traffic

try:
    from .traffic_camera import TrafficLightCamera
    from .frame_feeder import MultiCameraFeeder
    from .frame_consumer import FrameConsumer
    _CORE_TRAFFIC_CAMERA_AVAILABLE = True
except Exception as _import_exc:
    import logging as _log
    _log.warning("[carla_sync] Camera/consumer import failed: %s", _import_exc)
    TrafficLightCamera = None
    MultiCameraFeeder = None
    FrameConsumer = None
    _CORE_TRAFFIC_CAMERA_AVAILABLE = False

logger = logging.getLogger(__name__)


class SimulationSynchronization(object):
    """Synchronization of SUMO and CARLA simulations."""

    def __init__(self, sumo_simulation, carla_simulation, tls_manager="none",
                 sync_vehicle_color=False, sync_vehicle_lights=False):
        self.sumo = sumo_simulation
        self.carla = carla_simulation
        self.tls_manager = tls_manager
        self.sync_vehicle_color = sync_vehicle_color
        self.sync_vehicle_lights = sync_vehicle_lights
        if tls_manager == "carla":
            self.sumo.switch_off_traffic_lights()
        elif tls_manager == "sumo":
            self.carla.switch_off_traffic_lights()
        self.sumo2carla_ids = {}
        self.carla2sumo_ids = {}
        BridgeHelper.blueprint_library = self.carla.world.get_blueprint_library()
        BridgeHelper.offset = self.sumo.get_net_offset()
        settings = self.carla.world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = self.carla.step_length
        self.carla.world.apply_settings(settings)
        traffic_manager = self.carla.client.get_trafficmanager()
        traffic_manager.set_synchronous_mode(True)

    def tick(self):
        self.sumo.tick()
        sumo_spawned_actors = self.sumo.spawned_actors - set(self.carla2sumo_ids.values())
        for sumo_actor_id in sumo_spawned_actors:
            self.sumo.subscribe(sumo_actor_id)
            sumo_actor = self.sumo.get_actor(sumo_actor_id)
            carla_blueprint = BridgeHelper.get_carla_blueprint(sumo_actor, self.sync_vehicle_color)
            if carla_blueprint is not None:
                carla_transform = BridgeHelper.get_carla_transform(sumo_actor.transform, sumo_actor.extent)
                carla_actor_id = self.carla.spawn_actor(carla_blueprint, carla_transform)
                if carla_actor_id != INVALID_ACTOR_ID:
                    self.sumo2carla_ids[sumo_actor_id] = carla_actor_id
                else:
                    self.sumo.unsubscribe(sumo_actor_id)
        for sumo_actor_id in self.sumo.destroyed_actors:
            if sumo_actor_id in self.sumo2carla_ids:
                self.carla.destroy_actor(self.sumo2carla_ids.pop(sumo_actor_id))
        for sumo_actor_id in self.sumo2carla_ids:
            carla_actor_id = self.sumo2carla_ids[sumo_actor_id]
            sumo_actor = self.sumo.get_actor(sumo_actor_id)
            carla_transform = BridgeHelper.get_carla_transform(sumo_actor.transform, sumo_actor.extent)
            carla_lights = None
            if self.sync_vehicle_lights:
                carla_actor = self.carla.get_actor(carla_actor_id)
                carla_lights = BridgeHelper.get_carla_lights_state(
                    carla_actor.get_light_state(), sumo_actor.signals
                )
            self.carla.synchronize_vehicle(carla_actor_id, carla_transform, carla_lights)
        if self.tls_manager == "sumo":
            common_landmarks = self.sumo.traffic_light_ids & self.carla.traffic_light_ids
            for landmark_id in common_landmarks:
                sumo_tl_state = self.sumo.get_traffic_light_state(landmark_id)
                carla_tl_state = BridgeHelper.get_carla_traffic_light_state(sumo_tl_state)
                self.carla.synchronize_traffic_light(landmark_id, carla_tl_state)
        self.carla.tick()
        carla_spawned_actors = self.carla.spawned_actors - set(self.sumo2carla_ids.values())
        for carla_actor_id in carla_spawned_actors:
            carla_actor = self.carla.get_actor(carla_actor_id)
            type_id = BridgeHelper.get_sumo_vtype(carla_actor)
            color = carla_actor.attributes.get("color", None) if self.sync_vehicle_color else None
            if type_id is not None:
                sumo_actor_id = self.sumo.spawn_actor(type_id, color)
                if sumo_actor_id != INVALID_ACTOR_ID:
                    self.carla2sumo_ids[carla_actor_id] = sumo_actor_id
                    self.sumo.subscribe(sumo_actor_id)
        for carla_actor_id in self.carla.destroyed_actors:
            if carla_actor_id in self.carla2sumo_ids:
                self.sumo.destroy_actor(self.carla2sumo_ids.pop(carla_actor_id))
        for carla_actor_id in self.carla2sumo_ids:
            sumo_actor_id = self.carla2sumo_ids[carla_actor_id]
            carla_actor = self.carla.get_actor(carla_actor_id)
            sumo_actor = self.sumo.get_actor(sumo_actor_id)
            sumo_transform = BridgeHelper.get_sumo_transform(
                carla_actor.get_transform(), carla_actor.bounding_box.extent
            )
            sumo_lights = None
            if self.sync_vehicle_lights:
                carla_lights = self.carla.get_actor_light_state(carla_actor_id)
                if carla_lights is not None:
                    sumo_lights = BridgeHelper.get_sumo_lights_state(
                        sumo_actor.signals, carla_lights
                    )
            self.sumo.synchronize_vehicle(sumo_actor_id, sumo_transform, sumo_lights)
        if self.tls_manager == "carla":
            common_landmarks = self.sumo.traffic_light_ids & self.carla.traffic_light_ids
            for landmark_id in common_landmarks:
                carla_tl_state = self.carla.get_traffic_light_state(landmark_id)
                sumo_tl_state = BridgeHelper.get_sumo_traffic_light_state(carla_tl_state)
                self.sumo.synchronize_traffic_light(landmark_id, sumo_tl_state)

    def close(self):
        settings = self.carla.world.get_settings()
        settings.synchronous_mode = False
        settings.fixed_delta_seconds = None
        self.carla.world.apply_settings(settings)
        for carla_actor_id in self.sumo2carla_ids.values():
            self.carla.destroy_actor(carla_actor_id)
        for sumo_actor_id in self.carla2sumo_ids.values():
            self.sumo.destroy_actor(sumo_actor_id)
        self.carla.close()
        self.sumo.close()


# ---------------------------------------------------------------------------
# Geometry-driven camera placement
# ---------------------------------------------------------------------------
#
# CARLA traffic-light actor IDs are assigned at spawn time and aren't stable
# across sessions. Hardcoding IDs (70/71/72/73 + per-ID yaw/offset dicts) was
# fragile — a different session assigns different IDs and the cameras all
# collapse to the fallback anchor. Instead, discover the intersection's
# traffic-light group at runtime and derive each camera's placement from its
# own geometry relative to the group centroid.
# ---------------------------------------------------------------------------

def _group_traffic_lights(world) -> list[list]:
    """Return groups of traffic lights in the current CARLA world."""
    all_tls = list(world.get_actors().filter("traffic.traffic_light*"))
    visited: set[int] = set()
    groups: list[list] = []
    for tl in all_tls:
        if tl.id in visited:
            continue
        try:
            g = list(tl.get_group_traffic_lights())
        except Exception:
            g = [tl]
        for t in g:
            visited.add(t.id)
        groups.append(g)
    return groups


def _centroid_xy(group) -> tuple[float, float]:
    locs = [t.get_location() for t in group]
    cx = sum(l.x for l in locs) / len(locs)
    cy = sum(l.y for l in locs) / len(locs)
    return cx, cy


def _pick_intersection_group(world, target_xy: Optional[tuple[float, float]] = None):
    """Pick the best traffic-light group for cameras.

    If target_xy is provided, picks the group whose centroid is closest.
    Otherwise scores by quadrant coverage × group size (favouring 4-way junctions).
    Returns None if no groups exist.
    """
    groups = _group_traffic_lights(world)
    if not groups:
        return None

    if target_xy is not None:
        tx, ty = target_xy
        return min(
            groups,
            key=lambda g: (lambda c: (c[0] - tx) ** 2 + (c[1] - ty) ** 2)(_centroid_xy(g)),
        )

    def score(g) -> float:
        cx, cy = _centroid_xy(g)
        locs = [t.get_location() for t in g]
        angles = [math.degrees(math.atan2(l.y - cy, l.x - cx)) % 360 for l in locs]
        quads = len(set(int(a / 90) for a in angles))
        return quads * 100 + len(g)

    return max(groups, key=score)


_COMPASS_8 = ("east", "ne", "north", "nw", "west", "sw", "south", "se")


def _compass_label(angle_deg: float) -> str:
    """Map a [0, 360) angle to an 8-direction compass label.

    Junctions in Town03 are often diagonally oriented — their 4 lights sit at
    NE/NW/SE/SW corners rather than N/E/S/W. A 4-direction snap collapses
    diagonal corners into the same label. Using 8 directions keeps each corner
    distinct no matter the junction orientation.
    """
    a = angle_deg % 360
    # Each 45° slice centred on a cardinal direction.
    bucket = int(((a + 22.5) % 360) // 45)
    return _COMPASS_8[bucket]


def _compute_camera_placements(group, camera_height: float = 8.0,
                               forward_offset_m: float = 0.0,
                               lateral_offset_m: float = 0.0,
                               pitch_deg: float = -20.0,
                               yaw_offset_deg: float = 0.0
                               ) -> list[tuple[str, "carla.Transform"]]:
    """Derive (label, carla.Transform) for each light in the group.

    Each camera is mounted on its traffic light's pole and looks in the SAME
    direction as the light's bulb — which is the direction drivers approaching
    the intersection on the lane this light controls are coming from. So the
    camera naturally frames "the stop line + its approach lane", like a real
    red-light / enforcement camera.

    Parameters
    ----------
    camera_height : float
        Metres above the traffic-light actor's ground location.
    forward_offset_m : float
        Metres to shift the camera ALONG its facing direction (+ = out toward
        the lane it's watching; − = back into the intersection).
    lateral_offset_m : float
        Metres to shift the camera PERPENDICULAR to its facing direction, to
        centre it over the lane set. + = camera's right, − = camera's left.
        Poles usually sit at one edge of the road, so a few metres of lateral
        offset is typical to centre the view.
    pitch_deg : float
        Downward pitch. −20° frames the road surface without losing context.
    yaw_offset_deg : float
        Added to the light's yaw. Common values: 0, 90, 180, 270 depending on
        how CARLA authored the light's forward direction on your map.
    """
    import carla
    placements: list[tuple[str, carla.Transform]] = []
    used_labels: dict[str, int] = {}
    for tl in group:
        tl_transform = tl.get_transform()
        tl_loc = tl_transform.location
        tl_yaw = tl_transform.rotation.yaw

        # Camera yaw matches the light's own facing direction (plus optional
        # offset for maps where the reported yaw is 90/180/270 from expected).
        camera_yaw = tl_yaw + yaw_offset_deg

        # Forward unit vector (CARLA yaw 0° = +X, left-handed coord system →
        # yaw 90° = +Y). Right vector is 90° CW from forward.
        yaw_rad = math.radians(camera_yaw)
        fx = math.cos(yaw_rad)
        fy = math.sin(yaw_rad)
        # Camera's right = rotate forward 90° clockwise in CARLA's Z-up
        # left-handed system: (fx, fy) → (-fy, fx) gives left, so right is
        # (fy, -fx). We use the driver-intuitive convention "+ = right".
        rx = fy
        ry = -fx

        cam_x = tl_loc.x + fx * forward_offset_m + rx * lateral_offset_m
        cam_y = tl_loc.y + fy * forward_offset_m + ry * lateral_offset_m
        cam_z = tl_loc.z + camera_height

        # Label by the compass direction the camera is *pointing* — that's the
        # approach lane this feed is watching.
        label = _compass_label(camera_yaw)
        used_labels[label] = used_labels.get(label, 0) + 1
        if used_labels[label] > 1:
            label = f"{label}_{used_labels[label]}"

        transform = carla.Transform(
            carla.Location(x=cam_x, y=cam_y, z=cam_z),
            carla.Rotation(pitch=pitch_deg, yaw=camera_yaw, roll=0.0),
        )
        placements.append((label, transform))

    return placements


def run_sync_loop(args, emission_dir: Path, scenario_dir: Path):
    """Run CARLA-SUMO co-simulation loop. emission_dir and scenario_dir are absolute."""
    sumo_simulation = SumoSimulation(
        args.sumo_cfg_file, args.step_length, args.sumo_host,
        args.sumo_port, args.sumo_gui, args.client_order,
        extra_args=getattr(args, "sumo_extra_args", None),
    )
    carla_simulation = CarlaSimulation(args.carla_host, args.carla_port, args.step_length)
    synchronization = SimulationSynchronization(
        sumo_simulation, carla_simulation, args.tls_manager,
        args.sync_vehicle_color, args.sync_vehicle_lights,
    )

    # -----------------------------------------------------------------------
    # Traffic control setup
    # -----------------------------------------------------------------------
    control_scope = getattr(args, "control_scope", "single")
    enable_control = getattr(args, "enable_traffic_control", False)
    log_dir = Path(getattr(args, "log_dir", str(scenario_dir / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    emission_dir.mkdir(parents=True, exist_ok=True)

    # Config values are in real-world seconds; convert to simulation steps.
    _sl = args.step_length  # e.g. 0.05 s/step for CARLA
    def _sec_to_steps(attr: str, default_sec: float) -> int:
        return max(1, int(getattr(args, attr, default_sec) / _sl))

    phase_interval        = _sec_to_steps("controller_phase_interval", 10)
    emission_interval_steps = _sec_to_steps("controller_emission_interval", 50)
    cooldown              = _sec_to_steps("controller_cooldown", 60)
    min_green             = _sec_to_steps("controller_min_green", 20)
    yellow_duration       = _sec_to_steps("controller_yellow_duration", 5)
    max_red_steps         = _sec_to_steps("controller_max_red_steps", 300)
    weights = getattr(args, "controller_weights", None)

    logger.info(
        "CARLA co-sim: step_length=%.3fs — phase_interval=%d steps, "
        "cooldown=%d steps, min_green=%d steps, yellow=%d steps",
        _sl, phase_interval, cooldown, min_green, yellow_duration,
    )

    # Network-wide controller (Carbon-Emission-Traffic scenario)
    network_controller: Optional[core_traffic.NetworkController] = None
    passive_green_states: dict = {}
    baseline_mode = getattr(args, "baseline_mode", False)
    if enable_control and control_scope == "all":
        import traci as _traci
        tls_ids = getattr(args, "tls_ids", None)

        if baseline_mode:
            network_controller = core_traffic.FixedCycleController(
                tls_ids=tls_ids,
                phases=getattr(args, "baseline_phases", [0, 1, 2, 4]),
                green_steps=max(1, int(getattr(args, "baseline_green_seconds", 30) / _sl)),
                yellow_steps=max(1, int(getattr(args, "baseline_yellow_seconds", 5) / _sl)),
            )
            logger.info(
                "CARLA co-sim: BASELINE fixed-cycle controller (tls_ids=%s, "
                "green=%.0fs, yellow=%.0fs)",
                tls_ids,
                getattr(args, "baseline_green_seconds", 30),
                getattr(args, "baseline_yellow_seconds", 5),
            )
        else:
            network_controller = core_traffic.NetworkController(
                tls_ids=tls_ids,
                weights=weights,
                cooldown=cooldown,
                min_green=min_green,
                yellow_duration=yellow_duration,
                max_red_steps=max_red_steps,
            )
            logger.info(
                "CARLA co-sim: network-wide controller active (tls_ids=%s, "
                "phase_interval=%d steps, emission_interval=%d steps)",
                tls_ids, phase_interval, emission_interval_steps,
            )

        # Pin every non-controlled intersection to all-green so they never
        # obstruct traffic. State is re-applied every step to prevent SUMO's
        # own program from overriding it.
        controlled = set(tls_ids or [])
        passive_ids = [t for t in _traci.trafficlight.getIDList() if t not in controlled]
        passive_green_states = core_traffic.make_tls_always_green(passive_ids)
        logger.info("Passive TLS pinned to all-green: %s", passive_ids)

    # Camera setup — geometry-driven (ignores the legacy ID-based lists and
    # instead discovers the target junction's traffic-light group at runtime).
    traffic_cameras = []
    frame_consumer = None
    camera_feeder = None
    camera_labels: list[str] = []
    if getattr(args, "enable_camera", False):
        if _CORE_TRAFFIC_CAMERA_AVAILABLE:
            camera_feeder = MultiCameraFeeder()

        try:
            # Resolve target intersection in CARLA world coordinates. Priority:
            #   1. Explicit args.camera_target_xy (env override).
            #   2. SUMO junction position for the controlled TLS, transformed
            #      via BridgeHelper.offset — this is the "right" one because
            #      it's the junction SUMO is actually running logic on.
            #   3. None → _pick_intersection_group falls back to best-scored
            #      4-way group in the world.
            target_xy = getattr(args, "camera_target_xy", None)
            if target_xy is None:
                args_tls_ids = getattr(args, "tls_ids", None) or []
                sumo_tls = (
                    (args_tls_ids[0] if args_tls_ids else None)
                    or getattr(args, "tls_id", None)
                )
                if sumo_tls:
                    try:
                        import traci as _traci
                        sx, sy = _traci.junction.getPosition(sumo_tls)
                        ox, oy = BridgeHelper.offset
                        # SUMO→CARLA: carla.x = sumo.x - off.x ; carla.y = -(sumo.y - off.y)
                        carla_x = sx - ox
                        carla_y = -(sy - oy)
                        target_xy = (carla_x, carla_y)
                        logger.info(
                            "Camera target: SUMO junction %s at SUMO (%.1f, %.1f) "
                            "→ CARLA (%.1f, %.1f)",
                            sumo_tls, sx, sy, carla_x, carla_y,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Could not resolve SUMO junction %s position: %s — "
                            "falling back to best-scored 4-way group.",
                            sumo_tls, exc,
                        )
            group = _pick_intersection_group(carla_simulation.world, target_xy=target_xy)
            if group is None:
                logger.warning("Camera setup: no traffic-light groups found in world.")
            else:
                cx, cy = _centroid_xy(group)
                placements = _compute_camera_placements(
                    group,
                    camera_height=getattr(args, "camera_height_m", 8.0),
                    forward_offset_m=getattr(args, "camera_forward_m", 0.0),
                    lateral_offset_m=getattr(args, "camera_lateral_m", 0.0),
                    pitch_deg=getattr(args, "camera_pitch_deg", -20.0),
                    yaw_offset_deg=getattr(args, "camera_yaw_offset_deg", 0.0),
                )
                logger.info(
                    "Camera setup: group of %d lights at centroid (%.1f, %.1f) — "
                    "placing %d cameras.",
                    len(group), cx, cy, len(placements),
                )
                base_out = getattr(args, "camera_output_dir", "camera_output")
                for label, transform in placements:
                    traffic_cameras.append(
                        TrafficLightCamera(
                            carla_simulation.world,
                            tls_id=label,
                            output_dir=os.path.join(base_out, f"camera_{label}"),
                            save_interval=20,
                            frame_callback=(camera_feeder.on_frame if camera_feeder is not None else None),
                            camera_transform=transform,
                        )
                    )
                    camera_labels.append(label)
                logger.info("Camera labels (compass-direction-based): %s", camera_labels)
        except Exception as e:
            logger.warning("Camera setup failed: %s", e, exc_info=True)

    # Start frame consumer if cameras active
    if (getattr(args, "enable_camera", False)
            and _CORE_TRAFFIC_CAMERA_AVAILABLE
            and camera_feeder is not None
            and FrameConsumer is not None):
        frame_consumer = FrameConsumer(
            camera_feeder,
            output_dir=os.path.join(getattr(args, "camera_output_dir", "camera_output"), "consumer"),
            poll_interval=0.5,
            save_every=2,
            show_roi_box=getattr(args, "camera_show_roi", True),
        )
        frame_consumer.start()

        if network_controller is not None and not baseline_mode:
            cam_phase_map = getattr(args, "cam_phase_map", None)
            if cam_phase_map:
                network_controller.set_frame_consumer(frame_consumer, cam_phase_map)
            else:
                logger.warning(
                    "Cameras active but no cam_phase_map configured — "
                    "traffic control will fall back to TraCI emission scoring."
                )

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    step = 0
    try:
        while True:
            start = time.time()
            synchronization.tick()

            if passive_green_states:
                core_traffic.apply_always_green(passive_green_states)

            if enable_control:
                if network_controller is not None:
                    # Network-wide: uses all lights, writes full telemetry
                    core_traffic.step_network(
                        network_controller,
                        step,
                        str(emission_dir),
                        str(log_dir),
                        phase_interval=phase_interval,
                        emission_interval=emission_interval_steps,
                    )
                else:
                    # Single-intersection backward-compat path
                    core_traffic.step(args.tls_id, step, str(emission_dir))

            step += 1
            elapsed = time.time() - start
            if elapsed < args.step_length:
                time.sleep(args.step_length - elapsed)

    except KeyboardInterrupt:
        logger.info("Cancelled by user.")
    except Exception as exc:
        logger.error("Simulation loop error: %s", exc)
    finally:
        if frame_consumer is not None:
            frame_consumer.stop()
        if network_controller is not None:
            network_controller.write_run_summary(
                log_dir,
                additional={"total_steps": step, "mode": "carla"},
            )
        for cam in traffic_cameras:
            try:
                cam.destroy()
            except Exception:
                pass
        try:
            synchronization.close()
        except Exception:
            pass