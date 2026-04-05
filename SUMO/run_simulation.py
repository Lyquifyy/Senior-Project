#!/usr/bin/env python
"""
Central entry point for SUMO simulations (standalone and CARLA co-simulation).
Usage:
  python run_simulation.py --scenario Rev-5 --mode standalone
  python run_simulation.py --scenario Rev-5 --mode carla --sumo-gui --enable-traffic-control
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# SUMO dir and path setup
_SUMO_DIR = Path(__file__).resolve().parent
if str(_SUMO_DIR) not in sys.path:
    sys.path.insert(0, str(_SUMO_DIR))
if 'SUMO_HOME' in os.environ:
    sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))
else:
    pass  # traci will fail later if not set

logger = logging.getLogger(__name__)


def load_scenario_config(scenario: str) -> tuple:
    """Resolve scenario dir and load scenario.json. Returns (scenario_dir Path, config dict)."""
    scenarios_json_path = _SUMO_DIR / "scenarios.json"
    if scenarios_json_path.exists():
        with open(scenarios_json_path, encoding="utf-8") as f:
            scenarios = json.load(f)
        subpath = scenarios.get(scenario)
        if subpath is None:
            # Allow scenario to be a path like "CARLA/Rev-4_CARLA"
            subpath = scenario
    else:
        subpath = scenario
    scenario_dir = _SUMO_DIR / subpath
    if not scenario_dir.is_dir():
        raise FileNotFoundError(f"Scenario directory not found: {scenario_dir}")
    config_path = scenario_dir / "scenario.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Scenario config not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    return scenario_dir, config


def run_standalone_mode(scenario_dir: Path, config: dict, args) -> None:
    """Run standalone SUMO with core traffic control and optional trip generation."""
    from core.traffic_control import run_standalone as run_traci_loop
    from core import trip_generator

    sumocfg = config["sumocfg"]
    tls_id = args.tls_id or config.get("tls_id", "238")
    emission_dir = scenario_dir / config.get("emission_dir", "emissionData")
    emission_dir.mkdir(parents=True, exist_ok=True)

    if "csv_file" in config and "net_file" in config:
        csv_path = scenario_dir / config["csv_file"]
        net_path = scenario_dir / config["net_file"]
        if csv_path.exists() and net_path.exists():
            logger.info("Generating trips for scenario...")
            trip_generator.generate_trips(
                csv_file=str(csv_path),
                net_file=str(net_path),
                sim_end=getattr(args, 'sim_end', 500),
                heavyCO2Percent=getattr(args, 'heavy_co2', 0.3),
                threshold=getattr(args, 'threshold', 250),
                output_dir=str(scenario_dir),
            )

    sumo_cfg_absolute = str(scenario_dir / sumocfg)
    sumo_binary = "sumo-gui" if getattr(args, 'sumo_gui', False) else "sumo"
    sumo_cmd = [sumo_binary, "-c", sumo_cfg_absolute]
    run_traci_loop(
        sumo_cmd,
        tls_id=tls_id,
        emission_dir=str(emission_dir),
        step_interval_phase=10,
        step_interval_emissions=1,
        gui_sleep=0.5,
    )


def run_carla_mode(scenario_dir: Path, config: dict, args) -> None:
    """Run CARLA-SUMO co-simulation using core sync loop and traffic control."""
    from core.carla_sync import run_sync_loop
    from core import trip_generator

    sumocfg = config["sumocfg"]
    tls_id = args.tls_id or config.get("tls_id", "238")
    emission_dir = scenario_dir / config.get("emission_dir", "emissionData")
    emission_dir.mkdir(parents=True, exist_ok=True)

    if "csv_file" in config and "net_file" in config:
        csv_path = scenario_dir / config["csv_file"]
        net_path = scenario_dir / config["net_file"]
        if csv_path.exists() and net_path.exists():
            logger.info("Generating trips for CARLA scenario...")
            trip_generator.generate_trips(
                csv_file=str(csv_path),
                net_file=str(net_path),
                sim_end=getattr(args, 'sim_end', 300),
                heavyCO2Percent=getattr(args, 'heavy_co2', 0.3),
                threshold=getattr(args, 'threshold', 250),
                output_dir=str(scenario_dir),
            )

    args.sumo_cfg_file = str(scenario_dir / sumocfg)
    args.tls_id = tls_id
    run_sync_loop(args, emission_dir, scenario_dir)


def main():
    parser = argparse.ArgumentParser(description="Central SUMO simulation runner")
    parser.add_argument("--scenario", required=True, help="Scenario name (e.g. Rev-4, Rev-5, TraCI-Testing, Rev-4_CARLA)")
    parser.add_argument("--mode", choices=["standalone", "carla"], default="standalone", help="Run mode")
    parser.add_argument("--tls-id", default=None, help="Override traffic light ID from scenario.json")
    # Standalone options
    parser.add_argument("--sim-end", type=int, default=500, help="Simulation end time for trip generation")
    parser.add_argument("--heavy-co2", type=float, default=0.3, help="Heavy CO2 vehicle fraction (0-1)")
    parser.add_argument("--threshold", type=int, default=250, help="CO2 threshold for trip generator")
    # CARLA / SUMO shared
    parser.add_argument("--sumo-gui", action="store_true", help="Use sumo-gui instead of sumo")
    # CARLA-only
    parser.add_argument("--carla-host", default="127.0.0.1", help="CARLA server host")
    parser.add_argument("--carla-port", type=int, default=2000, help="CARLA server port")
    parser.add_argument("--sumo-host", default=None, help="SUMO server host (default: start new)")
    parser.add_argument("--sumo-port", type=int, default=None, help="SUMO server port")
    parser.add_argument("--step-length", type=float, default=0.05, help="Fixed delta seconds")
    parser.add_argument("--client-order", type=int, default=1, help="TraCI client order")
    parser.add_argument("--sync-vehicle-lights", action="store_true")
    parser.add_argument("--sync-vehicle-color", action="store_true")
    parser.add_argument("--sync-vehicle-all", action="store_true")
    parser.add_argument("--tls-manager", choices=["none", "sumo", "carla"], default="none")
    parser.add_argument("--enable-traffic-control", action="store_true", help="Enable traffic control plugin")
    parser.add_argument("--enable-camera", action="store_true")
    parser.add_argument("--camera-tls-id", default="70")
    parser.add_argument("--camera-tls-ids", default=None)
    parser.add_argument("--camera-output-dir", default="camera_output")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()
    if args.sync_vehicle_all:
        args.sync_vehicle_lights = True
        args.sync_vehicle_color = True

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not os.environ.get("SUMO_HOME"):
        sys.exit("Set SUMO_HOME environment variable.")

    scenario_dir, config = load_scenario_config(args.scenario)
    logger.info("Scenario: %s, mode: %s, dir: %s", args.scenario, args.mode, scenario_dir)

    if args.mode == "standalone":
        run_standalone_mode(scenario_dir, config, args)
    else:
        run_carla_mode(scenario_dir, config, args)


if __name__ == "__main__":
    main()
