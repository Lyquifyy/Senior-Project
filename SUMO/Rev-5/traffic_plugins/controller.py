"""
Traffic control plugin wrapper for CARLA-SUMO co-simulation.
Imports from SUMO core and delegates to core.traffic_control.step().
"""

import sys
from pathlib import Path

# Add SUMO dir to path so we can import core (controller is in Rev-5/traffic_plugins/)
_SUMO_DIR = Path(__file__).resolve().parent.parent.parent
if str(_SUMO_DIR) not in sys.path:
    sys.path.insert(0, str(_SUMO_DIR))

try:
    from core.traffic_control import (
        change_light_phase,
        decide_next_phase,
        collect_lane_emissions,
        compute_lane_metrics,
        map_lane_to_phase,
        PHASE_LANE_MAP,
        step as _core_step,
    )
except ImportError as e:
    raise ImportError(f"Failed to import from SUMO core: {e}. Ensure SUMO/ is on PYTHONPATH.") from e

# Default emission dir when plugin is used from Rev-5/run_synchronization.py (no central runner)
_DEFAULT_EMISSION_DIR = Path(__file__).resolve().parent.parent / "emissionData"


def step(tls_id: str, simulation_step: int, emission_dir=None) -> None:
    """
    Main step function called by run_synchronization.py during co-simulation.
    Delegates to core.traffic_control.step(). emission_dir defaults to Rev-5/emissionData.
    """
    if emission_dir is None:
        emission_dir = str(_DEFAULT_EMISSION_DIR)
    _core_step(
        tls_id,
        simulation_step,
        emission_dir,
        step_interval_phase=200,
        step_interval_emissions=400,
    )


__all__ = [
    "step",
    "change_light_phase",
    "decide_next_phase",
    "collect_lane_emissions",
    "compute_lane_metrics",
    "map_lane_to_phase",
    "PHASE_LANE_MAP",
]
