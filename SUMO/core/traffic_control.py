"""Central traffic light control for SUMO (standalone and CARLA co-simulation).

Two control modes are provided:

  Single-intersection (backward compatible)
      Used by Rev-4, Rev-5, TraCI-Testing.  All original functions are kept
      with unchanged signatures: change_light_phase, map_lane_to_phase,
      compute_lane_metrics, decide_next_phase, collect_lane_emissions,
      run_standalone, step.

  Network-wide emission-aware (Carbon-Emission-Traffic and future scenarios)
      IntersectionController  – adaptive per-TLS controller that discovers
          lane/phase mapping from TraCI at runtime.
      NetworkController       – manages every (or a specified subset of) TLS
          in the network, writes telemetry and decision logs.
      run_standalone_network  – standalone SUMO loop using network control.
      step_network            – single-step helper for CARLA co-sim.

The network controller uses live TraCI getCO2Emission / getFuelConsumption as
the actual carbon term (mg/s). The old static customCO2 parameter is kept as
scenario metadata only.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backward-compatible single-intersection helpers (Rev-4, Rev-5, TraCI-Testing)
# ---------------------------------------------------------------------------

PHASE_LANE_MAP = {
    1: {
        "description": "Northbound and Southbound through + right turns",
        "lanes": ["N_in_through", "N_in_right", "S_in_through", "S_in_right"],
    },
    5: {
        "description": "Eastbound and Westbound through + right turns",
        "lanes": ["E_in_through", "E_in_right", "W_in_through", "W_in_right"],
    },
}


def change_light_phase(tls_id: str) -> None:
    """Advance the traffic light to the next phase (wraps around)."""
    import traci
    current_phase = traci.trafficlight.getPhase(tls_id)
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    num_phases = len(programs[0].phases)
    next_phase = (current_phase + 1) % num_phases
    traci.trafficlight.setPhase(tls_id, next_phase)


def map_lane_to_phase(lane_id: str) -> Optional[int]:
    """Return the phase index mapped to a lane id, or None if not mapped."""
    for phase, info in PHASE_LANE_MAP.items():
        if lane_id in info["lanes"]:
            return phase
    return None


def compute_lane_metrics(tls_id: str) -> Dict[str, Dict[str, float]]:
    """Compute metrics per controlled lane. Returns {lane_id: {'queue': float, 'co2': float}}."""
    import traci
    lanes = traci.trafficlight.getControlledLanes(tls_id)
    metrics: Dict[str, Dict[str, float]] = {}
    for lane_id in lanes:
        veh_ids = traci.lane.getLastStepVehicleIDs(lane_id)
        total_co2 = 0.0
        queue_len = 0
        for vid in veh_ids:
            vtype_id = traci.vehicle.getTypeID(vid)
            try:
                custom_co2 = traci.vehicletype.getParameter(vtype_id, "customCO2")
            except Exception:
                custom_co2 = None
            if custom_co2:
                try:
                    total_co2 += float(custom_co2)
                except ValueError:
                    pass
            try:
                if traci.vehicle.getSpeed(vid) < 0.1:
                    queue_len += 1
            except Exception:
                pass
        metrics[lane_id] = {"queue": float(queue_len), "co2": float(total_co2)}
    return metrics


def decide_next_phase(tls_id: str) -> int:
    """Decide next phase from lane metrics (0.6*queue + 0.4*co2). Falls back to next phase."""
    import traci
    current_phase = traci.trafficlight.getPhase(tls_id)
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    num_phases = len(programs[0].phases)
    default_next = (current_phase + 1) % num_phases
    if current_phase in [1, 5]:
        metrics = compute_lane_metrics(tls_id)
        if not metrics:
            return default_next
        scores = {lane: 0.6 * data["queue"] + 0.4 * data["co2"] for lane, data in metrics.items()}
        best_lane = max(scores, key=scores.get)
        mapped_phase = map_lane_to_phase(best_lane)
        if mapped_phase is not None:
            return mapped_phase
    return default_next


def collect_lane_emissions(
    tls_id: str, step: int, emission_dir: Union[str, Path]
) -> None:
    """Collect emission data per lane and write JSON. emission_dir created if needed."""
    import traci
    emission_path = Path(emission_dir)
    emission_path.mkdir(parents=True, exist_ok=True)
    lanes = traci.trafficlight.getControlledLanes(tls_id)
    lane_emissions: Dict[str, List[Dict]] = {}
    for lane_id in lanes:
        veh_ids = traci.lane.getLastStepVehicleIDs(lane_id)
        lane_emissions[lane_id] = []
        for vid in veh_ids:
            try:
                data = {
                    "vehicle_id": vid,
                    "co2": traci.vehicle.getCO2Emission(vid),
                    "nox": traci.vehicle.getNOxEmission(vid),
                    "fuel": traci.vehicle.getFuelConsumption(vid),
                    "speed": traci.vehicle.getSpeed(vid),
                }
            except Exception:
                data = {"vehicle_id": vid}
            lane_emissions[lane_id].append(data)
    snapshot = {"step": step, "intersection": tls_id, "lanes": lane_emissions}
    file_path = emission_path / f"lane_emissions_step_{step}.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)


def run_standalone(
    sumo_cmd: List[str],
    tls_id: str,
    emission_dir: Union[str, Path],
    step_interval_phase: int = 10,
    step_interval_emissions: int = 50,
    gui_sleep: float = 0.5,
) -> None:
    """Run standalone SUMO loop with single-intersection traffic control."""
    import traci
    logger.info("Starting SUMO standalone: %s", sumo_cmd)
    traci.start(sumo_cmd)
    try:
        step = 0
        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            if gui_sleep > 0:
                time.sleep(gui_sleep)
            if step_interval_phase and step % step_interval_phase == 0 and step > 0:
                next_phase = decide_next_phase(tls_id)
                traci.trafficlight.setPhase(tls_id, next_phase)
            if step_interval_emissions and step % step_interval_emissions == 0 and step > 0:
                collect_lane_emissions(tls_id, step, emission_dir)
            step += 1
    finally:
        logger.info("Closing traci connection")
        traci.close()


def step(
    tls_id: str,
    simulation_step: int,
    emission_dir: Union[str, Path],
    step_interval_phase: int = 200,
    step_interval_emissions: int = 400,
) -> None:
    """Single step for CARLA co-sim plugin: phase decision and emission collection."""
    import traci
    if step_interval_phase and simulation_step % step_interval_phase == 0 and simulation_step > 0:
        next_phase = decide_next_phase(tls_id)
        traci.trafficlight.setPhase(tls_id, next_phase)
    if step_interval_emissions and simulation_step % step_interval_emissions == 0 and simulation_step > 0:
        collect_lane_emissions(tls_id, simulation_step, emission_dir)


# ---------------------------------------------------------------------------
# Network-wide controller internals
# ---------------------------------------------------------------------------

def _build_tls_phase_map(tls_id: str) -> Dict[int, List[str]]:
    """Discover which unique lanes receive green signal in each phase.

    Returns {phase_idx: [lane_id, ...]} only for phases that include at least
    one 'G' or 'g' signal.  Uses getControlledLanes (index == link index) and
    phase state strings from the first program.
    """
    import traci
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    if not programs:
        return {}
    program = programs[0]
    controlled_lanes = list(traci.trafficlight.getControlledLanes(tls_id))
    phase_map: Dict[int, List[str]] = {}
    for phase_idx, phase in enumerate(program.phases):
        state = phase.state
        green_indices = [i for i, c in enumerate(state) if c in ("G", "g")]
        if not green_indices:
            continue
        # dict.fromkeys preserves insertion order and de-duplicates
        lanes = list(dict.fromkeys(
            controlled_lanes[i] for i in green_indices if i < len(controlled_lanes)
        ))
        if lanes:
            phase_map[phase_idx] = lanes
    return phase_map


def _collect_phase_metrics(phase_map: Dict[int, List[str]]) -> Dict[int, Dict]:
    """Aggregate live TraCI emission and congestion metrics per phase.

    Values are normalized by number of lanes in each phase to avoid
    larger phases always dominating the score.
    """
    import traci
    result: Dict[int, Dict] = {}
    for phase_idx, lanes in phase_map.items():
        raw_co2 = 0.0
        raw_fuel = 0.0
        raw_nox = 0.0
        queue = 0
        waiting = 0.0
        vehicles = 0
        for lane_id in lanes:
            for vid in traci.lane.getLastStepVehicleIDs(lane_id):
                vehicles += 1
                try:
                    raw_co2 += traci.vehicle.getCO2Emission(vid)
                    raw_fuel += traci.vehicle.getFuelConsumption(vid)
                    raw_nox += traci.vehicle.getNOxEmission(vid)
                    waiting += traci.vehicle.getWaitingTime(vid)
                    if traci.vehicle.getSpeed(vid) < 0.1:
                        queue += 1
                except Exception:
                    pass
        n = max(len(lanes), 1)
        result[phase_idx] = {
            "co2": raw_co2 / n,
            "fuel": raw_fuel / n,
            "nox": raw_nox / n,
            "queue": queue / n,
            "wait": waiting / n,
            "vehicles": vehicles,
            "raw_co2": raw_co2,
            "raw_fuel": raw_fuel,
            "raw_queue": queue,
            "raw_wait": waiting,
        }
    return result


def _score_phases(
    phase_metrics: Dict[int, Dict],
    weights: Dict[str, float],
) -> Dict[int, float]:
    """Compute a weighted score per phase from normalized metrics."""
    return {
        phase_idx: (
            weights.get("queue", 0.4) * m["queue"]
            + weights.get("wait", 0.3) * m["wait"]
            + weights.get("co2", 0.2) * m["co2"]
            + weights.get("fuel", 0.1) * m["fuel"]
        )
        for phase_idx, m in phase_metrics.items()
    }


# ---------------------------------------------------------------------------
# Telemetry writers
# ---------------------------------------------------------------------------

def _write_network_snapshot(snapshot: Dict, telemetry_dir: Union[str, Path]) -> None:
    p = Path(telemetry_dir)
    p.mkdir(parents=True, exist_ok=True)
    step = snapshot.get("step", 0)
    with (p / f"network_step_{step}.json").open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)


def _append_decision_log(decisions: List[Dict], log_dir: Union[str, Path]) -> None:
    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)
    with (p / "phase_decisions.jsonl").open("a", encoding="utf-8") as f:
        for record in decisions:
            f.write(json.dumps(record) + "\n")


def _write_run_summary(summary: Dict, log_dir: Union[str, Path]) -> None:
    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)
    with (p / "run_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    logger.info("Run summary written to %s/run_summary.json", log_dir)


# ---------------------------------------------------------------------------
# Per-intersection adaptive controller
# ---------------------------------------------------------------------------

class IntersectionController:
    """Emission-aware adaptive controller for a single traffic-light junction.

    Discovers the lane/phase mapping lazily from TraCI on the first decision
    call, so it can be constructed before traci.start() is called.
    """

    def __init__(
        self,
        tls_id: str,
        cooldown: int = 30,
        min_green: int = 10,
    ) -> None:
        self.tls_id = tls_id
        self.cooldown = cooldown
        self.min_green = min_green
        self._phase_map: Dict[int, List[str]] = {}
        self._initialized = False
        self._last_switch_step: int = -(cooldown + 1)

    def _ensure_init(self) -> None:
        if not self._initialized:
            self._phase_map = _build_tls_phase_map(self.tls_id)
            self._initialized = True
            logger.debug(
                "TLS %s: discovered %d green phases, lane counts: %s",
                self.tls_id,
                len(self._phase_map),
                {k: len(v) for k, v in self._phase_map.items()},
            )

    def decide(
        self, current_step: int, weights: Dict[str, float]
    ) -> Tuple[int, Dict, bool]:
        """Return (chosen_phase, phase_scores, did_switch_attempt).

        Will not switch if cooldown has not elapsed since the last switch.
        The caller is responsible for actually calling traci.setPhase.
        """
        import traci
        self._ensure_init()
        current_phase = traci.trafficlight.getPhase(self.tls_id)
        programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(self.tls_id)
        num_phases = len(programs[0].phases)

        if current_step - self._last_switch_step < self.cooldown:
            return current_phase, {}, False

        if not self._phase_map:
            default_next = (current_phase + 1) % num_phases
            self._last_switch_step = current_step
            return default_next, {}, True

        phase_metrics = _collect_phase_metrics(self._phase_map)
        phase_scores = _score_phases(phase_metrics, weights)
        if not phase_scores:
            return current_phase, {}, False

        best_phase = max(phase_scores, key=phase_scores.get)
        score_export = {
            str(p): {
                "score": round(phase_scores[p], 4),
                **{k: round(v, 4) for k, v in phase_metrics.get(p, {}).items()},
            }
            for p in phase_scores
        }
        self._last_switch_step = current_step
        return best_phase, score_export, True

    def collect_lane_data(self) -> Dict[str, List[Dict]]:
        """Return per-lane vehicle emission and motion data for all controlled lanes."""
        import traci
        self._ensure_init()
        lanes = set(traci.trafficlight.getControlledLanes(self.tls_id))
        result: Dict[str, List[Dict]] = {}
        for lane_id in lanes:
            result[lane_id] = []
            for vid in traci.lane.getLastStepVehicleIDs(lane_id):
                try:
                    result[lane_id].append({
                        "vehicle_id": vid,
                        "co2": traci.vehicle.getCO2Emission(vid),
                        "nox": traci.vehicle.getNOxEmission(vid),
                        "fuel": traci.vehicle.getFuelConsumption(vid),
                        "speed": traci.vehicle.getSpeed(vid),
                        "waiting_time": traci.vehicle.getWaitingTime(vid),
                    })
                except Exception:
                    result[lane_id].append({"vehicle_id": vid})
        return result


# ---------------------------------------------------------------------------
# Network-wide controller
# ---------------------------------------------------------------------------

class NetworkController:
    """Emission-aware adaptive controller for all (or specified) traffic lights.

    Instantiate once before the simulation loop; call step_all() every tick.
    The controller is lazy: it calls traci only after the first step_all call,
    which happens after traci.start() has been called.

    Telemetry files written:
      <telemetry_dir>/network_step_<N>.json  – full network snapshot
      <log_dir>/phase_decisions.jsonl        – per-decision records (JSONL)
      <log_dir>/run_summary.json             – written when write_run_summary()
                                               is called at the end of a run
    """

    def __init__(
        self,
        tls_ids: Optional[List[str]] = None,
        weights: Optional[Dict[str, float]] = None,
        cooldown: int = 30,
        min_green: int = 10,
    ) -> None:
        self.tls_ids = tls_ids          # None → discover all at first step
        self.weights = weights or {"queue": 0.4, "wait": 0.3, "co2": 0.2, "fuel": 0.1}
        self.cooldown = cooldown
        self.min_green = min_green
        self._controllers: Dict[str, IntersectionController] = {}
        self._initialized = False
        # Cumulative run metrics (updated each emit snapshot)
        self._cumulative_co2_mg = 0.0
        self._cumulative_fuel_ml = 0.0
        self._snapshot_count = 0

    def _init(self) -> None:
        import traci
        ids = self.tls_ids or list(traci.trafficlight.getIDList())
        for tls_id in ids:
            self._controllers[tls_id] = IntersectionController(
                tls_id, cooldown=self.cooldown, min_green=self.min_green
            )
        self._initialized = True
        logger.info(
            "NetworkController initialized for %d intersections: %s",
            len(self._controllers),
            list(self._controllers.keys()),
        )

    def step_all(
        self,
        simulation_step: int,
        telemetry_dir: Union[str, Path],
        log_dir: Optional[Union[str, Path]] = None,
        phase_interval: int = 10,
        emission_interval: int = 50,
    ) -> Dict:
        """Run one simulation tick across all managed intersections.

        Returns the full network snapshot dict (contains per-TLS and totals).
        Writes telemetry and decision log files according to the intervals.
        """
        import traci
        if not self._initialized:
            self._init()

        do_phase = (
            phase_interval > 0
            and simulation_step % phase_interval == 0
            and simulation_step > 0
        )
        do_emit = (
            emission_interval > 0
            and simulation_step % emission_interval == 0
            and simulation_step > 0
        )

        tls_data: Dict[str, Dict] = {}
        decisions: List[Dict] = []
        net_co2 = 0.0
        net_fuel = 0.0
        net_nox = 0.0
        net_queue = 0
        net_vehicles = 0
        net_wait = 0.0

        for tls_id, ctrl in self._controllers.items():
            try:
                current_phase = traci.trafficlight.getPhase(tls_id)
            except Exception:
                logger.warning("Could not get phase for TLS %s; skipping.", tls_id)
                continue

            chosen_phase = current_phase
            phase_scores: Dict = {}
            switched = False

            if do_phase:
                chosen_phase, phase_scores, attempted = ctrl.decide(
                    simulation_step, self.weights
                )
                if attempted and chosen_phase != current_phase:
                    try:
                        traci.trafficlight.setPhase(tls_id, chosen_phase)
                        switched = True
                    except Exception as exc:
                        logger.warning(
                            "Failed to set phase %d for TLS %s: %s",
                            chosen_phase, tls_id, exc,
                        )
                        chosen_phase = current_phase

                decisions.append({
                    "step": simulation_step,
                    "tls_id": tls_id,
                    "current_phase": current_phase,
                    "selected_phase": chosen_phase,
                    "switch_executed": switched,
                    "phase_scores": phase_scores,
                })

            lane_data: Dict[str, List[Dict]] = {}
            if do_emit:
                lane_data = ctrl.collect_lane_data()

            tls_co2 = sum(v.get("co2", 0) for vlist in lane_data.values() for v in vlist)
            tls_fuel = sum(v.get("fuel", 0) for vlist in lane_data.values() for v in vlist)
            tls_nox = sum(v.get("nox", 0) for vlist in lane_data.values() for v in vlist)
            tls_veh = sum(len(vlist) for vlist in lane_data.values())
            tls_queue = sum(
                1 for vlist in lane_data.values() for v in vlist
                if v.get("speed", 1.0) < 0.1
            )
            tls_wait = sum(
                v.get("waiting_time", 0) for vlist in lane_data.values() for v in vlist
            )

            tls_data[tls_id] = {
                "current_phase": current_phase,
                "chosen_phase": chosen_phase,
                "switched": switched,
                "phase_scores": phase_scores,
                "lanes": lane_data,
                "co2_mg": tls_co2,
                "co2_kg": round(tls_co2 / 1_000_000, 6),
                "fuel_ml": tls_fuel,
                "nox_mg": tls_nox,
                "vehicle_count": tls_veh,
                "queue": tls_queue,
                "total_waiting_time": tls_wait,
            }

            net_co2 += tls_co2
            net_fuel += tls_fuel
            net_nox += tls_nox
            net_vehicles += tls_veh
            net_queue += tls_queue
            net_wait += tls_wait

        network_snapshot = {
            "step": simulation_step,
            "tls": tls_data,
            "network_totals": {
                "co2_mg": net_co2,
                "co2_kg": round(net_co2 / 1_000_000, 6),
                "fuel_ml": net_fuel,
                "nox_mg": net_nox,
                "queue": net_queue,
                "vehicles": net_vehicles,
                "total_waiting_time": net_wait,
            },
        }

        if do_emit:
            _write_network_snapshot(network_snapshot, telemetry_dir)
            self._cumulative_co2_mg += net_co2
            self._cumulative_fuel_ml += net_fuel
            self._snapshot_count += 1

        if log_dir and decisions and do_phase:
            _append_decision_log(decisions, log_dir)

        return network_snapshot

    def write_run_summary(
        self,
        log_dir: Union[str, Path],
        additional: Optional[Dict] = None,
    ) -> None:
        """Write end-of-run summary JSON with cumulative emission totals."""
        summary: Dict = {
            "total_co2_kg": round(self._cumulative_co2_mg / 1_000_000, 4),
            "total_fuel_ml": round(self._cumulative_fuel_ml, 2),
            "emission_snapshots_collected": self._snapshot_count,
            "controlled_intersections": list(self._controllers.keys()),
            "weights_used": self.weights,
            "cooldown_steps": self.cooldown,
        }
        if additional:
            summary.update(additional)
        _write_run_summary(summary, log_dir)


# ---------------------------------------------------------------------------
# Network standalone runner
# ---------------------------------------------------------------------------

def run_standalone_network(
    sumo_cmd: List[str],
    tls_ids: Optional[List[str]],
    telemetry_dir: Union[str, Path],
    log_dir: Union[str, Path],
    phase_interval: int = 10,
    emission_interval: int = 50,
    cooldown: int = 30,
    weights: Optional[Dict[str, float]] = None,
    gui_sleep: float = 0.0,
) -> None:
    """Run SUMO standalone with network-wide emission-aware traffic control.

    Writes network_step_*.json to telemetry_dir, phase_decisions.jsonl and
    run_summary.json to log_dir.  Built-in SUMO outputs (emission-output,
    tripinfo-output, summary-output) must be enabled in the .sumocfg.
    """
    import traci
    controller = NetworkController(
        tls_ids=tls_ids,
        weights=weights,
        cooldown=cooldown,
    )
    logger.info("Starting network-wide SUMO: %s", " ".join(sumo_cmd))
    traci.start(sumo_cmd)
    step = 0
    arrived = 0
    try:
        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            if gui_sleep > 0:
                time.sleep(gui_sleep)
            controller.step_all(
                step,
                telemetry_dir,
                log_dir,
                phase_interval=phase_interval,
                emission_interval=emission_interval,
            )
            arrived += traci.simulation.getArrivedNumber()
            step += 1
    finally:
        logger.info(
            "Network simulation finished after %d steps. Vehicles completed: %d.",
            step, arrived,
        )
        controller.write_run_summary(
            log_dir,
            additional={
                "total_steps": step,
                "total_vehicles_completed": arrived,
                "phase_interval": phase_interval,
                "emission_interval": emission_interval,
            },
        )
        logger.info("Closing traci connection")
        traci.close()


def step_network(
    network_controller: "NetworkController",
    simulation_step: int,
    telemetry_dir: Union[str, Path],
    log_dir: Optional[Union[str, Path]] = None,
    phase_interval: int = 200,
    emission_interval: int = 400,
) -> Dict:
    """Single-step helper for CARLA co-sim using a shared NetworkController.

    The controller must be constructed before the CARLA loop and passed in
    each tick so that per-TLS state (last switch step) persists across ticks.
    """
    return network_controller.step_all(
        simulation_step,
        telemetry_dir,
        log_dir,
        phase_interval=phase_interval,
        emission_interval=emission_interval,
    )


__all__ = [
    # Backward-compatible single-intersection API
    "change_light_phase",
    "decide_next_phase",
    "collect_lane_emissions",
    "compute_lane_metrics",
    "map_lane_to_phase",
    "run_standalone",
    "step",
    "PHASE_LANE_MAP",
    # Network-wide API
    "IntersectionController",
    "NetworkController",
    "run_standalone_network",
    "step_network",
    # Telemetry helpers
    "_write_network_snapshot",
    "_append_decision_log",
    "_write_run_summary",
    "_build_tls_phase_map",
    "_collect_phase_metrics",
    "_score_phases",
]
