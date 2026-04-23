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


def _find_yellow_phases(tls_id: str) -> List[int]:
    """Return phase indices that are pure yellow/red-yellow transitions (no green signals)."""
    import traci
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    if not programs:
        return []
    yellow = []
    for idx, phase in enumerate(programs[0].phases):
        state = phase.state.lower()
        if "y" in state and "g" not in state:
            yellow.append(idx)
    return yellow


def _synthesize_yellow_state(state: str) -> str:
    """Convert a green phase state string to yellow by replacing G/g with y."""
    return "".join("y" if c in ("G", "g") else c for c in state)


def _collect_phase_metrics_from_cameras(
    frame_consumer,
    cam_phase_map: Dict[str, int],
) -> Dict[int, Dict]:
    """Aggregate camera-based vehicle count and emission score per phase.

    cam_phase_map: {cam_id (str) -> phase_idx (int)}
    Emission score = emission_weight(dominant_type) * vehicle_count.
    """
    phase_data: Dict[int, Dict] = {}
    for cam_id, phase_idx in cam_phase_map.items():
        summary = frame_consumer.get_approach_summary(str(cam_id))
        if phase_idx not in phase_data:
            phase_data[phase_idx] = {"emission_score": 0.0, "vehicle_count": 0, "dominant_type": None}
        phase_data[phase_idx]["emission_score"] += summary.get("emission_score", 0.0)
        phase_data[phase_idx]["vehicle_count"]  += summary.get("vehicle_count", 0)
        if summary.get("dominant_type"):
            phase_data[phase_idx]["dominant_type"] = summary["dominant_type"]
    return phase_data


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
        yellow_duration: int = 3,
        max_red_steps: int = 300,
        frame_consumer=None,
        cam_phase_map: Optional[Dict[str, int]] = None,
    ) -> None:
        self.tls_id = tls_id
        self.cooldown = cooldown
        self.min_green = min_green
        self.yellow_duration = yellow_duration
        self.max_red_steps = max_red_steps
        self._frame_consumer = frame_consumer
        self._cam_phase_map: Dict[str, int] = cam_phase_map or {}
        self._phase_map: Dict[int, List[str]] = {}
        self._yellow_phases: List[int] = []
        self._initialized = False
        self._last_switch_step: int = -(cooldown + 1)
        self._current_phase_start: int = 0
        self._pending_phase: Optional[int] = None
        self._yellow_end_step: int = 0
        self._phase_last_green: Dict[int, int] = {}

    def _ensure_init(self) -> None:
        if not self._initialized:
            self._phase_map = _build_tls_phase_map(self.tls_id)
            self._yellow_phases = _find_yellow_phases(self.tls_id)
            self._initialized = True
            logger.debug(
                "TLS %s: discovered %d green phases, %d yellow phases, lane counts: %s",
                self.tls_id,
                len(self._phase_map),
                len(self._yellow_phases),
                {k: len(v) for k, v in self._phase_map.items()},
            )

    @property
    def is_transitioning(self) -> bool:
        """True while a yellow intergreen is in progress."""
        return self._pending_phase is not None

    def tick(self, current_step: int) -> bool:
        """Apply the pending target phase once the yellow duration has elapsed.

        Must be called every simulation step. Returns True if a phase was applied.
        """
        if self._pending_phase is None or current_step < self._yellow_end_step:
            return False
        import traci
        target = self._pending_phase
        self._pending_phase = None
        try:
            traci.trafficlight.setPhase(self.tls_id, target)
            self._phase_last_green[target] = current_step
            self._current_phase_start = current_step
        except Exception as exc:
            logger.warning(
                "TLS %s: failed to apply pending phase %d after yellow: %s",
                self.tls_id, target, exc,
            )
        return True

    def decide(
        self, current_step: int, weights: Dict[str, float]
    ) -> Tuple[int, Dict, bool, str]:
        """Return (chosen_phase, phase_scores, did_switch_attempt, decision_reason).

        decision_reason is one of:
          "score"       – weighted scoring selected a different phase
          "score_hold"  – weighted scoring kept the current phase
          "starvation"  – starvation override selected the most-overdue phase
          "no_phase_map"– no green phases found; advanced to next phase by default

        Blocks (returns attempted=False) when: a yellow transition is in progress,
        min_green has not elapsed, or cooldown has not elapsed.

        When switching phases, inserts a yellow intergreen automatically if yellow
        phases exist in the signal program — the caller must NOT call setPhase when
        is_transitioning is True after this returns.
        """
        import traci
        self._ensure_init()
        current_phase = traci.trafficlight.getPhase(self.tls_id)

        # Block new decisions while yellow transition is in progress
        if self._pending_phase is not None:
            return current_phase, {}, False, ""

        # Enforce minimum green time
        if current_step - self._current_phase_start < self.min_green:
            return current_phase, {}, False, ""

        # Enforce cooldown
        if current_step - self._last_switch_step < self.cooldown:
            return current_phase, {}, False, ""

        programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(self.tls_id)
        num_phases = len(programs[0].phases)

        if not self._phase_map:
            default_next = (current_phase + 1) % num_phases
            self._last_switch_step = current_step
            self._current_phase_start = current_step
            return default_next, {}, True, "no_phase_map"

        if self._frame_consumer and self._cam_phase_map:
            cam_metrics = _collect_phase_metrics_from_cameras(
                self._frame_consumer, self._cam_phase_map
            )
            phase_scores = {idx: data["emission_score"] for idx, data in cam_metrics.items()}
            score_export = {
                str(p): {
                    "score": round(phase_scores.get(p, 0.0), 4),
                    "vehicle_count": cam_metrics.get(p, {}).get("vehicle_count", 0),
                    "dominant_type": cam_metrics.get(p, {}).get("dominant_type"),
                }
                for p in phase_scores
            }
            known_phases = set(self._cam_phase_map.values())
        else:
            phase_metrics = _collect_phase_metrics(self._phase_map)
            phase_scores = _score_phases(phase_metrics, weights)
            score_export = {
                str(p): {
                    "score": round(phase_scores.get(p, 0.0), 4),
                    **{k: round(v, 4) for k, v in phase_metrics.get(p, {}).items()},
                }
                for p in phase_scores
            }
            known_phases = set(self._phase_map.keys())

        if not phase_scores:
            return current_phase, {}, False, ""

        # Starvation prevention: if any phase hasn't had green in max_red_steps,
        # serve the most overdue one regardless of current scores.
        threshold = current_step - self.max_red_steps
        starved = [
            p for p in known_phases
            if p != current_phase
            and self._phase_last_green.get(p, current_step) < threshold
        ]
        if starved:
            best_phase = min(starved, key=lambda p: self._phase_last_green.get(p, 0))
            reason = "starvation"
        else:
            best_phase = max(phase_scores, key=phase_scores.get)
            reason = "score"

        self._last_switch_step = current_step

        if best_phase == current_phase:
            self._phase_last_green[current_phase] = current_step
            return current_phase, score_export, True, "score_hold"

        logger.info(
            "TLS %s step %d: %s phase %d→%d | scores %s",
            self.tls_id, current_step, reason, current_phase, best_phase,
            {p: round(s, 3) for p, s in phase_scores.items()},
        )

        # Initiate transition: use a defined yellow phase if one exists,
        # otherwise synthesize yellow from the current green state string.
        if self._yellow_phases:
            yellow_idx = self._yellow_phases[0]
            try:
                traci.trafficlight.setPhase(self.tls_id, yellow_idx)
            except Exception as exc:
                logger.warning(
                    "TLS %s: failed to set yellow phase %d: %s",
                    self.tls_id, yellow_idx, exc,
                )
        else:
            try:
                current_state = programs[0].phases[current_phase].state
                traci.trafficlight.setRedYellowGreenState(
                    self.tls_id, _synthesize_yellow_state(current_state)
                )
            except Exception as exc:
                logger.warning(
                    "TLS %s: failed to set synthetic yellow state: %s",
                    self.tls_id, exc,
                )
        self._pending_phase = best_phase
        self._yellow_end_step = current_step + self.yellow_duration
        return best_phase, score_export, True, reason

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
        yellow_duration: int = 3,
        max_red_steps: int = 300,
        frame_consumer=None,
        cam_phase_map: Optional[Dict[str, int]] = None,
    ) -> None:
        self.tls_ids = tls_ids          # None → discover all at first step
        self.weights = weights or {"queue": 0.4, "wait": 0.3, "co2": 0.2, "fuel": 0.1}
        self.cooldown = cooldown
        self.min_green = min_green
        self.yellow_duration = yellow_duration
        self.max_red_steps = max_red_steps
        self._frame_consumer = frame_consumer
        self._cam_phase_map: Dict[str, int] = cam_phase_map or {}
        self._controllers: Dict[str, IntersectionController] = {}
        self._initialized = False
        # Cumulative run metrics (updated each emit snapshot)
        self._cumulative_co2_mg = 0.0
        self._cumulative_fuel_ml = 0.0
        self._snapshot_count = 0

    def set_frame_consumer(self, frame_consumer, cam_phase_map: Dict[str, int]) -> None:
        """Wire up camera-based scoring after the FrameConsumer has started.

        Must be called before the first step_all() tick.
        """
        self._frame_consumer = frame_consumer
        self._cam_phase_map = {str(k): int(v) for k, v in cam_phase_map.items()}
        logger.info(
            "NetworkController: camera scoring enabled — cam→phase map: %s",
            self._cam_phase_map,
        )

    def _init(self) -> None:
        import traci
        ids = self.tls_ids or list(traci.trafficlight.getIDList())
        for tls_id in ids:
            self._controllers[tls_id] = IntersectionController(
                tls_id,
                cooldown=self.cooldown,
                min_green=self.min_green,
                yellow_duration=self.yellow_duration,
                max_red_steps=self.max_red_steps,
                frame_consumer=self._frame_consumer,
                cam_phase_map=self._cam_phase_map if self._cam_phase_map else None,
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

            # Apply any pending yellow→target transition every step.
            ctrl.tick(simulation_step)

            chosen_phase = current_phase
            phase_scores: Dict = {}
            switched = False

            if do_phase:
                chosen_phase, phase_scores, attempted, decision_reason = ctrl.decide(
                    simulation_step, self.weights
                )
                if attempted and chosen_phase != current_phase:
                    # When a yellow transition was just initiated inside decide(),
                    # is_transitioning is True and the phase was already set there.
                    if not ctrl.is_transitioning:
                        try:
                            traci.trafficlight.setPhase(tls_id, chosen_phase)
                        except Exception as exc:
                            logger.warning(
                                "Failed to set phase %d for TLS %s: %s",
                                chosen_phase, tls_id, exc,
                            )
                            chosen_phase = current_phase
                    switched = True

                decisions.append({
                    "step": simulation_step,
                    "tls_id": tls_id,
                    "current_phase": current_phase,
                    "selected_phase": chosen_phase,
                    "switch_executed": switched,
                    "decision_reason": decision_reason,
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
    cooldown: int = 60,
    min_green: int = 20,
    weights: Optional[Dict[str, float]] = None,
    gui_sleep: float = 0.0,
    yellow_duration: int = 5,
    max_red_steps: int = 300,
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
        min_green=min_green,
        yellow_duration=yellow_duration,
        max_red_steps=max_red_steps,
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


# ---------------------------------------------------------------------------
# Passive intersection helpers (always-green for non-controlled TLS)
# ---------------------------------------------------------------------------

def make_tls_always_green(tls_ids: List[str]) -> Dict[str, str]:
    """Build the all-green state string for each TLS.

    Call once after traci.start(). Returns {tls_id: state_string} to pass
    to apply_always_green() every simulation step.
    """
    import traci
    green_states: Dict[str, str] = {}
    for tls_id in tls_ids:
        try:
            n = len(traci.trafficlight.getRedYellowGreenState(tls_id))
            green_states[tls_id] = "G" * n
            logger.info("TLS %s: %d signals pinned to all-green", tls_id, n)
        except Exception as exc:
            logger.warning("Could not read TLS %s state: %s", tls_id, exc)
    return green_states


def apply_always_green(green_states: Dict[str, str]) -> None:
    """Re-apply all-green states every step so SUMO's program cannot override them."""
    import traci
    for tls_id, state in green_states.items():
        try:
            traci.trafficlight.setRedYellowGreenState(tls_id, state)
        except Exception as exc:
            logger.debug("Could not apply green state to TLS %s: %s", tls_id, exc)


# ---------------------------------------------------------------------------
# Fixed-cycle controller (real-world baseline)
# ---------------------------------------------------------------------------

class FixedCycleController:
    """Fixed-cycle traffic light controller for real-world baseline comparison.

    Rotates through a defined list of green phases on a fixed timer, inserting
    a yellow intergreen between each.  Implements the same step_all /
    write_run_summary interface as NetworkController so carla_sync can use it
    as a drop-in replacement.

    Timing is supplied in simulation steps.  Convert from real-world seconds
    using the same _sec_to_steps helper used for NetworkController.

    Typical real-world suburban 4-way: 30 s green / 5 s yellow per phase
    → ~140 s full cycle.
    """

    def __init__(
        self,
        tls_ids: Optional[List[str]] = None,
        phases: Optional[List[int]] = None,
        green_steps: int = 600,
        yellow_steps: int = 100,
    ) -> None:
        self.tls_ids = tls_ids
        self.phases = phases or [0, 1, 2, 4]
        self.green_steps = green_steps
        self.yellow_steps = yellow_steps
        self._state: Dict[str, Dict] = {}
        self._initialized = False
        self._cumulative_co2_mg = 0.0
        self._cumulative_fuel_ml = 0.0
        self._snapshot_count = 0

    def _init(self) -> None:
        import traci
        ids = self.tls_ids or list(traci.trafficlight.getIDList())
        for tls_id in ids:
            self._state[tls_id] = {
                "phase_idx": 0,
                "steps_in_phase": 0,
                "in_yellow": False,
            }
            try:
                traci.trafficlight.setPhase(tls_id, self.phases[0])
            except Exception:
                pass
        self._initialized = True
        logger.info(
            "FixedCycleController: %d TLS | phases=%s | green=%d steps | yellow=%d steps",
            len(self._state), self.phases, self.green_steps, self.yellow_steps,
        )

    def step_all(
        self,
        simulation_step: int,
        telemetry_dir: Union[str, Path],
        log_dir: Optional[Union[str, Path]] = None,
        phase_interval: int = 1,
        emission_interval: int = 50,
    ) -> Dict:
        import traci
        if not self._initialized:
            self._init()

        decisions: List[Dict] = []
        net_co2 = 0.0
        net_fuel = 0.0

        for tls_id, state in self._state.items():
            try:
                current_phase = traci.trafficlight.getPhase(tls_id)
            except Exception:
                continue

            state["steps_in_phase"] += 1
            switched = False
            reason = ""

            if state["in_yellow"]:
                if state["steps_in_phase"] >= self.yellow_steps:
                    state["phase_idx"] = (state["phase_idx"] + 1) % len(self.phases)
                    next_phase = self.phases[state["phase_idx"]]
                    try:
                        traci.trafficlight.setPhase(tls_id, next_phase)
                    except Exception:
                        pass
                    state["in_yellow"] = False
                    state["steps_in_phase"] = 0
                    switched = True
                    reason = "fixed_cycle"
                    logger.debug("FixedCycle TLS %s → phase %d", tls_id, next_phase)
            else:
                if state["steps_in_phase"] >= self.green_steps:
                    yellow_phases = _find_yellow_phases(tls_id)
                    try:
                        if yellow_phases:
                            traci.trafficlight.setPhase(tls_id, yellow_phases[0])
                        else:
                            programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
                            cur_state = programs[0].phases[current_phase].state
                            traci.trafficlight.setRedYellowGreenState(
                                tls_id, _synthesize_yellow_state(cur_state)
                            )
                    except Exception:
                        pass
                    state["in_yellow"] = True
                    state["steps_in_phase"] = 0
                    switched = True
                    reason = "fixed_cycle_yellow"

            if switched and log_dir:
                decisions.append({
                    "step": simulation_step,
                    "tls_id": tls_id,
                    "current_phase": current_phase,
                    "selected_phase": self.phases[state["phase_idx"]],
                    "switch_executed": True,
                    "decision_reason": reason,
                    "phase_scores": {},
                })

        do_emit = emission_interval > 0 and simulation_step % emission_interval == 0 and simulation_step > 0
        if do_emit:
            for tls_id in self._state:
                try:
                    lanes = set(traci.trafficlight.getControlledLanes(tls_id))
                    for lane_id in lanes:
                        for vid in traci.lane.getLastStepVehicleIDs(lane_id):
                            net_co2  += traci.vehicle.getCO2Emission(vid)
                            net_fuel += traci.vehicle.getFuelConsumption(vid)
                except Exception:
                    pass
            self._cumulative_co2_mg  += net_co2
            self._cumulative_fuel_ml += net_fuel
            self._snapshot_count += 1

        if log_dir and decisions:
            _append_decision_log(decisions, log_dir)

        return {
            "step": simulation_step,
            "network_totals": {
                "co2_mg": net_co2,
                "co2_kg": round(net_co2 / 1_000_000, 6),
            },
        }

    def write_run_summary(
        self,
        log_dir: Union[str, Path],
        additional: Optional[Dict] = None,
    ) -> None:
        summary: Dict = {
            "controller_type": "fixed_cycle",
            "total_co2_kg": round(self._cumulative_co2_mg / 1_000_000, 4),
            "total_fuel_ml": round(self._cumulative_fuel_ml, 2),
            "emission_snapshots_collected": self._snapshot_count,
            "controlled_intersections": list(self._state.keys()),
            "fixed_cycle_phases": self.phases,
            "green_steps": self.green_steps,
            "yellow_steps": self.yellow_steps,
        }
        if additional:
            summary.update(additional)
        _write_run_summary(summary, log_dir)


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
    # Fixed-cycle baseline
    "FixedCycleController",
    # Passive intersection helpers (always-green)
    "make_tls_always_green",
    "apply_always_green",
    # Telemetry helpers
    "_write_network_snapshot",
    "_append_decision_log",
    "_write_run_summary",
    "_build_tls_phase_map",
    "_find_yellow_phases",
    "_synthesize_yellow_state",
    "_collect_phase_metrics",
    "_score_phases",
]
