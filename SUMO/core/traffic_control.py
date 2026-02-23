"""Central traffic light control for SUMO (standalone and CARLA co-simulation).

All paths (emission_dir) and tls_id are passed as arguments; no BASE_DIR.
Used by run_simulation.py and traffic_plugins/controller.py.
"""

from pathlib import Path
import json
import logging
from typing import Dict, Optional, List, Union

logger = logging.getLogger(__name__)

# Phase Mapping - defines which lanes are controlled by which phase (Town03-style)
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
    """Run standalone SUMO loop with traffic control. Requires traci already on path."""
    import traci
    import time
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


__all__ = [
    "change_light_phase",
    "decide_next_phase",
    "collect_lane_emissions",
    "compute_lane_metrics",
    "map_lane_to_phase",
    "run_standalone",
    "step",
    "PHASE_LANE_MAP",
]
