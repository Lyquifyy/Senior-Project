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

    # Camera setup (unchanged)
    traffic_cameras = []
    frame_consumer = None
    camera_feeder = None
    if getattr(args, "enable_camera", False):
        camera_feeder = None
        if _CORE_TRAFFIC_CAMERA_AVAILABLE:
            camera_feeder = MultiCameraFeeder()

        try:
            cam_ids_str = (
                getattr(args, "camera_tls_ids", None)
                or getattr(args, "camera_tls_id", None)
                or args.tls_id
            )
            camera_ids = [int(x.strip()) for x in str(cam_ids_str).split(",")]
            base_out = getattr(args, "camera_output_dir", "camera_output")
            for camera_id in camera_ids:
                out_dir = (
                    os.path.join(base_out, f"camera_{camera_id}")
                    if len(camera_ids) > 1
                    else base_out
                )
                traffic_cameras.append(
                    TrafficLightCamera(
                        carla_simulation.world,
                        tls_id=str(camera_id),
                        output_dir=out_dir,
                        save_interval=20,
                        frame_callback=(camera_feeder.on_frame if camera_feeder is not None else None),
                    )
                )
        except ImportError as e:
            logger.warning("Traffic camera not available: %s", e)

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