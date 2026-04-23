#!/usr/bin/env python
"""
Central entry point for SUMO simulations (standalone and CARLA co-simulation).

Usage:
  python run_simulation.py --scenario Rev-5 --mode standalone
  python run_simulation.py --scenario Carbon-Emission-Traffic --mode standalone --sumo-gui
  python run_simulation.py --scenario Rev-5 --mode carla --sumo-gui --enable-traffic-control

Scenarios with "control_scope": "all" in their scenario.json use the
network-wide NetworkController; all other scenarios use the original
single-intersection controller for backward compatibility.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_SUMO_DIR = Path(__file__).resolve().parent
if str(_SUMO_DIR) not in sys.path:
    sys.path.insert(0, str(_SUMO_DIR))
if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

logger = logging.getLogger(__name__)


def load_scenario_config(scenario: str) -> tuple:
    """Resolve scenario dir and load scenario.json. Returns (scenario_dir Path, config dict)."""
    scenarios_json_path = _SUMO_DIR / "scenarios.json"
    if scenarios_json_path.exists():
        with open(scenarios_json_path, encoding="utf-8") as f:
            scenarios = json.load(f)
        subpath = scenarios.get(scenario, scenario)
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


# ---------------------------------------------------------------------------
# Standalone runners
# ---------------------------------------------------------------------------

def _generate_trips_if_needed(scenario_dir: Path, config: dict, args) -> None:
    """Run trip generation when both csv_file and net_file are present in config."""
    from core import trip_generator
    if "csv_file" not in config or "net_file" not in config:
        return
    csv_path = scenario_dir / config["csv_file"]
    net_path = scenario_dir / config["net_file"]
    if csv_path.exists() and net_path.exists():
        logger.info("Generating trips for scenario (output_dir=%s)...", scenario_dir)
        trip_generator.generate_trips(
            csv_file=str(csv_path),
            net_file=str(net_path),
            sim_end=getattr(args, "sim_end", 500),
            heavyCO2Percent=getattr(args, "heavy_co2", 0.3),
            threshold=getattr(args, "threshold", 250),
            output_dir=str(scenario_dir),
        )
    else:
        logger.warning(
            "CSV or net file not found; skipping trip generation. "
            "(csv=%s, net=%s)", csv_path, net_path,
        )


def run_standalone_mode(scenario_dir: Path, config: dict, args) -> None:
    """Dispatch to single-intersection or network-wide standalone runner."""
    control_scope = config.get("control_scope", "single")
    if control_scope == "all":
        _run_standalone_network(scenario_dir, config, args)
    else:
        _run_standalone_single(scenario_dir, config, args)


def _run_standalone_single(scenario_dir: Path, config: dict, args) -> None:
    """Original single-intersection standalone runner (Rev-4, Rev-5, TraCI-Testing)."""
    from core.traffic_control import run_standalone as run_traci_loop

    sumocfg = config["sumocfg"]
    tls_id = args.tls_id or config.get("tls_id", "238")
    emission_dir = scenario_dir / config.get("emission_dir", "emissionData")
    emission_dir.mkdir(parents=True, exist_ok=True)

    _generate_trips_if_needed(scenario_dir, config, args)

    sumo_binary = "sumo-gui" if getattr(args, "sumo_gui", False) else "sumo"
    sumo_cmd = [sumo_binary, "-c", str(scenario_dir / sumocfg)]
    run_traci_loop(
        sumo_cmd,
        tls_id=tls_id,
        emission_dir=str(emission_dir),
        step_interval_phase=10,
        step_interval_emissions=50,
        gui_sleep=0.5 if getattr(args, "sumo_gui", False) else 0.0,
    )


def _run_standalone_network(scenario_dir: Path, config: dict, args) -> None:
    """Network-wide emission-aware standalone runner (Carbon-Emission-Traffic)."""
    from core.traffic_control import run_standalone_network

    sumocfg = config["sumocfg"]
    tls_ids = config.get("tls_ids") or None       # None → discover all at runtime
    emission_dir = scenario_dir / config.get("emission_dir", "emissionData")
    log_dir = scenario_dir / config.get("log_dir", "logs")
    emission_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    ctrl_cfg = config.get("controller", {})
    phase_interval = ctrl_cfg.get("phase_interval", 10)
    emission_interval = ctrl_cfg.get("emission_interval", 50)
    cooldown = ctrl_cfg.get("cooldown", 60)
    min_green = ctrl_cfg.get("min_green", 20)
    yellow_duration = ctrl_cfg.get("yellow_duration", 5)
    max_red_steps = ctrl_cfg.get("max_red_steps", 300)
    weights = ctrl_cfg.get("weights", None)

    _generate_trips_if_needed(scenario_dir, config, args)

    sumo_binary = "sumo-gui" if getattr(args, "sumo_gui", False) else "sumo"
    sumo_cmd = [sumo_binary, "-c", str(scenario_dir / sumocfg)]

    logger.info(
        "Launching network-wide controller: %d intersections, "
        "phase_interval=%d, emission_interval=%d, cooldown=%d, min_green=%d, yellow_duration=%d",
        len(tls_ids) if tls_ids else 0,
        phase_interval, emission_interval, cooldown, min_green, yellow_duration,
    )
    run_standalone_network(
        sumo_cmd=sumo_cmd,
        tls_ids=tls_ids,
        telemetry_dir=str(emission_dir),
        log_dir=str(log_dir),
        phase_interval=phase_interval,
        emission_interval=emission_interval,
        cooldown=cooldown,
        min_green=min_green,
        yellow_duration=yellow_duration,
        max_red_steps=max_red_steps,
        weights=weights,
        gui_sleep=0.5 if getattr(args, "sumo_gui", False) else 0.0,
    )


# ---------------------------------------------------------------------------
# CARLA runner
# ---------------------------------------------------------------------------

def run_carla_mode(scenario_dir: Path, config: dict, args) -> None:
    """Run CARLA-SUMO co-simulation using core sync loop and traffic control."""
    from core.carla_sync import run_sync_loop
    from core import trip_generator  # noqa: F401 – kept for side-effects on older scenarios

    sumocfg = config["sumocfg"]
    tls_id = args.tls_id or config.get("tls_id", "238")
    baseline_mode = getattr(args, "baseline", False)
    args.baseline_mode = baseline_mode

    # Use separate output dirs so adaptive and baseline logs don't overwrite each other
    dir_suffix = "_baseline" if baseline_mode else ""
    emission_dir = scenario_dir / (config.get("emission_dir", "emissionData") + dir_suffix)
    log_dir      = scenario_dir / (config.get("log_dir", "logs") + dir_suffix)
    emission_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Propagate network-control settings into args so carla_sync can read them
    args.control_scope = config.get("control_scope", "single")
    args.tls_ids = config.get("tls_ids")
    args.log_dir = str(log_dir)
    ctrl_cfg = config.get("controller", {})
    # All values stored as real-world seconds; carla_sync converts to steps.
    args.controller_phase_interval = ctrl_cfg.get("phase_interval", 10)
    args.controller_emission_interval = ctrl_cfg.get("emission_interval", 50)
    args.controller_cooldown = ctrl_cfg.get("cooldown", 60)
    args.controller_min_green = ctrl_cfg.get("min_green", 20)
    args.controller_yellow_duration = ctrl_cfg.get("yellow_duration", 5)
    args.controller_max_red_steps = ctrl_cfg.get("max_red_steps", 300)
    args.controller_weights = ctrl_cfg.get("weights")
    raw_map = ctrl_cfg.get("cam_phase_map")
    args.cam_phase_map = {str(k): int(v) for k, v in raw_map.items()} if raw_map else None

    # Baseline-specific settings
    baseline_cfg = config.get("baseline", {})
    args.baseline_phases       = baseline_cfg.get("phases", [0, 1, 2, 4])
    args.baseline_green_seconds  = baseline_cfg.get("green_seconds", 30)
    args.baseline_yellow_seconds = baseline_cfg.get("yellow_seconds", 5)

    # Override SUMO output paths so both runs' files coexist
    args.sumo_extra_args = [
        "--tripinfo-output", str(log_dir / "sumo_tripinfo.xml"),
        "--summary-output",  str(log_dir / "sumo_summary.xml"),
        "--emission-output", str(emission_dir / "sumo_emissions.xml"),
    ]

    _generate_trips_if_needed(scenario_dir, config, args)

    args.sumo_cfg_file = str(scenario_dir / sumocfg)
    args.tls_id = tls_id

    label = "BASELINE (fixed-cycle)" if baseline_mode else "ADAPTIVE (camera-weighted)"
    logger.info("Run mode: %s — logs → %s", label, log_dir)

    run_sync_loop(args, emission_dir, scenario_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Central SUMO simulation runner")
    parser.add_argument(
        "--scenario", required=True,
        help="Scenario name (e.g. Rev-4, Rev-5, Carbon-Emission-Traffic, Rev-4_CARLA)",
    )
    parser.add_argument(
        "--mode", choices=["standalone", "carla"], default="standalone",
        help="Run mode",
    )
    parser.add_argument(
        "--tls-id", default=None,
        help="Override traffic light ID from scenario.json (single-intersection mode only)",
    )
    # Standalone / trip generation options
    parser.add_argument("--sim-end", type=int, default=500, help="Simulation end time for trip generation")
    parser.add_argument("--heavy-co2", type=float, default=0.3, help="Heavy CO2 vehicle fraction (0-1)")
    parser.add_argument("--threshold", type=int, default=250, help="CO2 threshold (g/mi) for trip generator")
    # SUMO / CARLA shared
    parser.add_argument("--sumo-gui", action="store_true", help="Use sumo-gui instead of sumo")
    # CARLA-only
    parser.add_argument("--carla-host", default="127.0.0.1")
    parser.add_argument("--carla-port", type=int, default=2000)
    parser.add_argument("--sumo-host", default=None)
    parser.add_argument("--sumo-port", type=int, default=None)
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument("--client-order", type=int, default=1)
    parser.add_argument("--sync-vehicle-lights", action="store_true")
    parser.add_argument("--sync-vehicle-color", action="store_true")
    parser.add_argument("--sync-vehicle-all", action="store_true")
    parser.add_argument(
        "--tls-manager", choices=["none", "sumo", "carla"], default="none",
        help="Traffic light ownership in CARLA co-sim; use 'sumo' with --enable-traffic-control",
    )
    parser.add_argument("--enable-traffic-control", action="store_true")
    parser.add_argument(
        "--baseline", action="store_true",
        help="Run fixed-cycle baseline controller instead of adaptive camera-weighted controller",
    )
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
        sys.exit("Set SUMO_HOME environment variable before running.")

    scenario_dir, config = load_scenario_config(args.scenario)
    logger.info(
        "Scenario: %s | mode: %s | control_scope: %s | dir: %s",
        args.scenario, args.mode, config.get("control_scope", "single"), scenario_dir,
    )

    if args.mode == "standalone":
        run_standalone_mode(scenario_dir, config, args)
    else:
        run_carla_mode(scenario_dir, config, args)


if __name__ == "__main__":
    main()
