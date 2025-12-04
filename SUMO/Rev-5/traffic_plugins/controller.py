"""
Traffic control plugin wrapper for CARLA-SUMO co-simulation.

This imports functions from the master traffic_control.py
module. This allows us to edit one source file (traffic_control.py) and have
changes automatically reflected in both standalone SUMO and CARLA co-simulation.

"""

import sys
from pathlib import Path

# Add parent directory to path to import from Test/Rev-5/traffic_control.py
PARENT_DIR = Path(__file__).resolve().parent.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

# Import all core functions from the traffic_control module
try:
    from traffic_control import (
        change_light_phase,
        decide_next_phase,
        collect_lane_emissions,
        compute_lane_metrics,
        map_lane_to_phase,
        PHASE_LANE_MAP
    )
except ImportError as e:
    print(f"Error importing from traffic_control.py: {e}")
    print(f"Make sure traffic_control.py is in: {PARENT_DIR}")
    raise


# ==================================================================================================
# -- CO-SIMULATION INTERFACE -----------------------------------------------------------------------
# ==================================================================================================

def step(tls_id: str, simulation_step: int) -> None:
    """
    Main step function called by run_synchronization.py during co-simulation.
    
    Timing for co-simulation:
    - Every 20n steps (n seconds): Make intelligent phase decision
    - Every 20n steps (n seconds): Collect emission data
    
    Note: This is different from standalone SUMO which uses faster timing for testing.
    
    Args:
        tls_id: Traffic light ID to control (e.g., "238")
        simulation_step: Current simulation step number
    """
    # Every 200 steps (10 seconds), intelligently decide and switch phase
    if simulation_step % 200 == 0 and simulation_step > 0:
        next_phase = decide_next_phase(tls_id)
        import traci
        traci.trafficlight.setPhase(tls_id, next_phase)

    # Every 400 steps (20 seconds), collect emissions data for analysis
    if simulation_step % 400 == 0 and simulation_step > 0:
        collect_lane_emissions(tls_id, simulation_step)

# Re-export all functions for easy access if needed
__all__ = [
    'step',
    'change_light_phase',
    'decide_next_phase',
    'collect_lane_emissions',
    'compute_lane_metrics',
    'map_lane_to_phase',
    'PHASE_LANE_MAP'
]