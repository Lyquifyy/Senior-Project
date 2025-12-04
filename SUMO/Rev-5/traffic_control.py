"""Traffic light control helpers for the SUMO Rev scenario.

This module contains the master logic for traffic control that works both:
1. Standalone SUMO simulation (run this file directly)
2. CARLA co-simulation (imported by traffic_plugins/controller.py)
The timing of phase changes and emission collection differs between modes, but the core logic is shared.
"""

from pathlib import Path
import json
import logging
import os
import time
import argparse
from typing import Dict, Optional, List

import trip_generator
import traci

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
BASE_DIR = Path(__file__).resolve().parent
EMISSION_DIR = BASE_DIR / "emissionData"
SUMO_CONFIG = str(BASE_DIR / "Town03.sumocfg")
SUMO_CMD = ["sumo-gui", "-c", SUMO_CONFIG]

# Phase Mapping - defines which lanes are controlled by which phase
PHASE_LANE_MAP = {
    1: {  # Phase 1: North-South through/right
        "description": "Northbound and Southbound through + right turns",
        "lanes": ["N_in_through", "N_in_right", "S_in_through", "S_in_right"],
    },
    5: {  # Phase 5: East-West through/right
        "description": "Eastbound and Westbound through + right turns",
        "lanes": ["E_in_through", "E_in_right", "W_in_through", "W_in_right"],
    },
}


def change_light_phase(tls_id: str) -> None:
    """Advance the traffic light to the next phase (wraps around).

    This is a simple helper that queries the light program and sets the next
    phase index.
    
    Args:
        tls_id: Traffic light ID to control
    """
    current_phase = traci.trafficlight.getPhase(tls_id)
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    num_phases = len(programs[0].phases)
    next_phase = (current_phase + 1) % num_phases
    traci.trafficlight.setPhase(tls_id, next_phase)


def map_lane_to_phase(lane_id: str) -> Optional[int]:
    """Return the phase index mapped to a lane id, or None if not mapped.
    
    Args:
        lane_id: Lane identifier to look up
        
    Returns:
        Phase index (int) or None if lane not found in mapping
    """
    for phase, info in PHASE_LANE_MAP.items():
        if lane_id in info["lanes"]:
            return phase
    return None


def compute_lane_metrics(tls_id: str) -> Dict[str, Dict[str, float]]:
    """Compute simple metrics for each controlled lane.

    Returns a dict keyed by lane id with 'queue' and 'co2' values.
    
    Args:
        tls_id: Traffic light ID to analyze
        
    Returns:
        Dictionary: {lane_id: {'queue': float, 'co2': float}}
    """
    lanes = traci.trafficlight.getControlledLanes(tls_id)
    metrics: Dict[str, Dict[str, float]] = {}
    
    for lane_id in lanes:
        veh_ids: List[str] = traci.lane.getLastStepVehicleIDs(lane_id)
        total_co2 = 0.0
        queue_len = 0
        
        for vid in veh_ids:
            # Get custom CO2 parameter from vehicle type
            vtype_id = traci.vehicle.getTypeID(vid)
            try:
                custom_co2 = traci.vehicletype.getParameter(vtype_id, "customCO2")
            except Exception:
                custom_co2 = None
                
            if custom_co2:
                try:
                    total_co2 += float(custom_co2)
                except ValueError:
                    logger.debug("non-numeric customCO2 for %s: %r", vtype_id, custom_co2)
            
            # Count vehicles in queue (speed < 0.1 m/s)
            try:
                speed = traci.vehicle.getSpeed(vid)
            except Exception:
                speed = 0.0
            if speed < 0.1:
                queue_len += 1
                
        metrics[lane_id] = {"queue": float(queue_len), "co2": float(total_co2)}
        
    return metrics


def decide_next_phase(tls_id: str) -> int:
    """Decide which phase to set next based on lane metrics.

    The function favors lanes with higher weighted score = 0.6*queue + 0.4*co2.
    If no useful metric is available it falls back to the next phase index.
    
    Args:
        tls_id: Traffic light ID to analyze
        
    Returns:
        Phase index to switch to
    """
    current_phase = traci.trafficlight.getPhase(tls_id)

    # Default fallback: advance by one and wrap using the program definition
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    num_phases = len(programs[0].phases)
    default_next = (current_phase + 1) % num_phases

    # Only try the heuristic for phases where a decision makes sense
    if current_phase in [1, 5]:
        metrics = compute_lane_metrics(tls_id)
        if not metrics:
            return default_next

        # Calculate weighted scores: 0.6*queue + 0.4*co2
        scores = {lane: 0.6 * data["queue"] + 0.4 * data["co2"] 
                  for lane, data in metrics.items()}
        
        # Find lane with highest priority
        best_lane = max(scores, key=scores.get)
        mapped_phase = map_lane_to_phase(best_lane)
        
        if mapped_phase is not None:
            return mapped_phase

    return default_next


def collect_lane_emissions(tls_id: str, step: int) -> None:
    """Collect emission and status data per lane and write a JSON snapshot.

    Creates the emission directory if it does not exist.
    
    Args:
        tls_id: Traffic light ID to monitor
        step: Current simulation step number
    """
    EMISSION_DIR.mkdir(parents=True, exist_ok=True)

    lanes = traci.trafficlight.getControlledLanes(tls_id)
    lane_emissions: Dict[str, List[Dict[str, float]]] = {}

    for lane_id in lanes:
        veh_ids: List[str] = traci.lane.getLastStepVehicleIDs(lane_id)
        lane_emissions[lane_id] = []
        
        for vid in veh_ids:
            # Use traci getters; if any fail keep the rest
            try:
                data = {
                    "vehicle_id": vid,
                    "co2": traci.vehicle.getCO2Emission(vid),
                    "nox": traci.vehicle.getNOxEmission(vid),
                    "fuel": traci.vehicle.getFuelConsumption(vid),
                    "speed": traci.vehicle.getSpeed(vid),
                }
            except Exception:
                logger.debug("failed to get emissions for vehicle %s", vid)
                data = {"vehicle_id": vid}
            lane_emissions[lane_id].append(data)

    emissions_snapshot = {
        "step": step, 
        "intersection": tls_id, 
        "lanes": lane_emissions
    }

    file_path = EMISSION_DIR / f"lane_emissions_step_{step}.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(emissions_snapshot, f, indent=2)


# ==================================================================================================
# -- STANDALONE SUMO SIMULATION MODE ---------------------------------------------------------------
# ==================================================================================================

def run_standalone(sumo_cmd: Optional[List[str]] = None, tls_id: str = "238") -> None:
    """Run the SUMO simulation loop and apply traffic control logic.

    This is for standalone SUMO mode (not CARLA co-simulation).
    Timing: Phase change every 10 steps, emissions every 50 steps.
    
    Args:
        sumo_cmd: SUMO command to execute (defaults to SUMO_CMD)
        tls_id: Traffic light ID to control (default "238")
    """
    if sumo_cmd is None:
        sumo_cmd = SUMO_CMD

    logger.info("Starting SUMO standalone with command: %s", sumo_cmd)
    traci.start(sumo_cmd)
    
    try:
        step = 0
        while traci.simulation.getMinExpectedNumber() > 0:
            # Advance the simulation by one step
            traci.simulationStep()
            
            # Optional: slow down GUI for visualization
            time.sleep(0.5)

            # Every 10 steps, switch to the next phase based on metrics
            if step % 10 == 0 and step > 0:
                next_phase = decide_next_phase(tls_id)
                traci.trafficlight.setPhase(tls_id, next_phase)

            # Every 50 steps, collect emissions data
            if step % 50 == 0 and step > 0:
                collect_lane_emissions(tls_id, step)

            step += 1
            
    finally:
        logger.info("Closing traci connection")
        traci.close()


# ==================================================================================================
# -- MAIN (Standalone Mode) ------------------------------------------------------------
# ==================================================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SUMO traffic control simulation")
    parser.add_argument("--sim-end", type=int, default=500, 
                        help="simulation end time for trip generation")
    parser.add_argument("--heavy-co2", type=float, default=0.3, 
                        help="percent heavy CO2 vehicles (0-1)")
    parser.add_argument("--threshold", type=int, default=250, 
                        help="CO2 threshold for heavy vehicle classification")
    parser.add_argument("--tls-id", type=str, default="238",
                        help="Traffic light ID to control")
    args = parser.parse_args()

    # Generate trips before running simulation
    logger.info("Generating trips...")
    rou_file, vtypes_file = trip_generator.generate_trips(
        csv_file=str(BASE_DIR / "cars.csv"),
        net_file=str(BASE_DIR / "Town03.net.xml"),
        sim_end=args.sim_end,
        heavyCO2Percent=args.heavy_co2,
        threshold=args.threshold,
    )

    # Run standalone SUMO simulation
    logger.info("Starting standalone SUMO simulation...")
    run_standalone(tls_id=args.tls_id)