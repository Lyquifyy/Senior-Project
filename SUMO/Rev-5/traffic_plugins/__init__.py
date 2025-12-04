"""
Traffic control plugins for CARLA-SUMO co-simulation.

This package provides traffic control functionality that works with both:
- Standalone SUMO simulation
- CARLA-SUMO co-simulation

The core logic lives in ../traffic_control.py (master file) and is imported
by controller.py for use in co-simulation.
"""

from .controller import (
    step,
    change_light_phase,
    decide_next_phase,
    collect_lane_emissions,
    compute_lane_metrics,
    map_lane_to_phase,
    PHASE_LANE_MAP
)

__all__ = [
    'step',
    'change_light_phase',
    'decide_next_phase',
    'collect_lane_emissions',
    'compute_lane_metrics',
    'map_lane_to_phase',
    'PHASE_LANE_MAP'
]

__version__ = '2.0.0'