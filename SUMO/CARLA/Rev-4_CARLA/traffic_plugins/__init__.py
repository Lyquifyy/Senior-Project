"""
Traffic control plugins for CARLA-SUMO co-simulation.

This package contains the traffic control logic that can be
plugged into the run_synchronization.py co-simulation script.
"""

from .traffic_controller import TrafficController

__all__ = ['TrafficController']