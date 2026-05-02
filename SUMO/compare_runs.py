"""
Compare adaptive (camera-weighted) vs baseline (fixed-cycle) simulation runs.

Usage:
  python compare_runs.py --scenario Carbon-Emission-Traffic
  python compare_runs.py --adaptive logs/ --baseline logs_baseline/

Reads sumo_tripinfo.xml and run_summary.json from each run's log directory
and prints a side-by-side report suitable for a research report.
"""

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


def parse_tripinfo(tripinfo_path: Path) -> dict:
    """Extract per-vehicle timing and emission stats from SUMO tripinfo XML."""
    if not tripinfo_path.exists():
        return {}

    tree = ET.parse(tripinfo_path)
    root = tree.getroot()

    waiting_times = []
    durations = []
    time_losses = []
    co2_mg_list = []  # CO2_abs is in mg per vehicle

    for trip in root.findall("tripinfo"):
        try:
            waiting_times.append(float(trip.get("waitingTime", 0)))
            durations.append(float(trip.get("duration", 0)))
            time_losses.append(float(trip.get("timeLoss", 0)))
        except (TypeError, ValueError):
            continue
        em = trip.find("emissions")
        if em is not None:
            try:
                co2_mg_list.append(float(em.get("CO2_abs", 0)))
            except (TypeError, ValueError):
                pass

    if not durations:
        return {}

    total_co2_kg = round(sum(co2_mg_list) / 1_000_000, 4) if co2_mg_list else None

    return {
        "vehicles_completed":  len(durations),
        "avg_waiting_time_s":  round(sum(waiting_times) / len(waiting_times), 2),
        "avg_trip_duration_s": round(sum(durations)     / len(durations),     2),
        "avg_time_loss_s":     round(sum(time_losses)   / len(time_losses),   2),
        "total_co2_kg":        total_co2_kg,
    }


def parse_run_summary(summary_path: Path) -> dict:
    """Extract timing config from run_summary.json (green/yellow step counts only)."""
    if not summary_path.exists():
        return {}
    with open(summary_path, encoding="utf-8") as f:
        data = json.load(f)
    # CO2 from run_summary is snapshot-based (unreliable); use tripinfo instead.
    return {
        "green_steps":  data.get("green_steps", None),
        "yellow_steps": data.get("yellow_steps", None),
    }


def _pct_change(adaptive, baseline) -> str:
    """Format percentage change (adaptive vs baseline). Negative = adaptive is better."""
    if baseline is None or baseline == 0 or adaptive is None:
        return "  n/a"
    pct = (adaptive - baseline) / baseline * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _co2_per_vehicle(total_co2_kg, vehicles) -> float | None:
    if total_co2_kg is None or not vehicles:
        return None
    return round(total_co2_kg * 1_000 / vehicles, 1)  # kg → g per vehicle


def print_report(adaptive: dict, baseline: dict, scenario: str = "") -> None:
    W = 70
    print("=" * W)
    title = "TRAFFIC LIGHT COMPARISON REPORT"
    if scenario:
        title += f"  |  {scenario}"
    print(f"  {title}")
    print(f"  Adaptive: camera-weighted emission scoring")
    print(f"  Baseline: fixed-cycle ({baseline.get('green_s', 30)}s green / "
          f"{baseline.get('yellow_s', 5)}s yellow per phase)")
    print("=" * W)
    print(f"{'Metric':<35} {'Adaptive':>12} {'Baseline':>12} {'Change':>8}")
    print("-" * W)

    rows = [
        ("Vehicles completed",       "vehicles_completed",    "",    False),
        ("Avg waiting time (s)",      "avg_waiting_time_s",   "s",   True),
        ("Avg trip duration (s)",     "avg_trip_duration_s",  "s",   True),
        ("Avg time loss (s)",         "avg_time_loss_s",      "s",   True),
        ("Total CO2 (kg)",            "total_co2_kg",         "kg",  True),
    ]

    for label, key, _unit, lower_is_better in rows:
        a_val = adaptive.get(key)
        b_val = baseline.get(key)
        a_str = f"{a_val:,.2f}" if isinstance(a_val, float) else (str(a_val) if a_val is not None else "n/a")
        b_str = f"{b_val:,.2f}" if isinstance(b_val, float) else (str(b_val) if b_val is not None else "n/a")
        chg   = _pct_change(a_val, b_val)
        print(f"  {label:<33} {a_str:>12} {b_str:>12} {chg:>8}")

    # CO2 per vehicle (derived)
    a_co2_per = _co2_per_vehicle(adaptive.get("total_co2_kg"), adaptive.get("vehicles_completed"))
    b_co2_per = _co2_per_vehicle(baseline.get("total_co2_kg"), baseline.get("vehicles_completed"))
    a_str = f"{a_co2_per:.1f} g" if a_co2_per else "n/a"
    b_str = f"{b_co2_per:.1f} g" if b_co2_per else "n/a"
    chg   = _pct_change(a_co2_per, b_co2_per)
    print(f"  {'CO2 per vehicle (g)':<33} {a_str:>12} {b_str:>12} {chg:>8}")

    print("-" * W)
    print("  Negative change = adaptive emits/waits less than baseline")
    print("=" * W)


def main():
    parser = argparse.ArgumentParser(description="Compare adaptive vs baseline simulation runs")
    parser.add_argument(
        "--scenario", default=None,
        help="Scenario name (resolves log dirs automatically from scenarios.json)",
    )
    parser.add_argument(
        "--adaptive", default=None,
        help="Path to adaptive run's log directory (contains sumo_tripinfo.xml, run_summary.json)",
    )
    parser.add_argument(
        "--baseline", default=None,
        help="Path to baseline run's log directory",
    )
    args = parser.parse_args()

    _SUMO_DIR = Path(__file__).resolve().parent

    # Resolve directories
    if args.scenario:
        scenarios_json = _SUMO_DIR / "scenarios.json"
        subpath = args.scenario
        if scenarios_json.exists():
            with open(scenarios_json) as f:
                subpath = json.load(f).get(args.scenario, args.scenario)
        scenario_dir = _SUMO_DIR / subpath
        adaptive_dir = scenario_dir / "logs"
        baseline_dir = scenario_dir / "logs_baseline"
    elif args.adaptive and args.baseline:
        adaptive_dir = Path(args.adaptive)
        baseline_dir = Path(args.baseline)
    else:
        parser.error("Provide --scenario or both --adaptive and --baseline directory paths.")

    # Parse data
    a_trip = parse_tripinfo(adaptive_dir / "sumo_tripinfo.xml")
    b_trip = parse_tripinfo(baseline_dir / "sumo_tripinfo.xml")
    a_summ = parse_run_summary(adaptive_dir / "run_summary.json")
    b_summ = parse_run_summary(baseline_dir / "run_summary.json")

    if not a_trip:
        print(f"WARNING: No tripinfo found at {adaptive_dir / 'sumo_tripinfo.xml'}", file=sys.stderr)
    if not b_trip:
        print(f"WARNING: No tripinfo found at {baseline_dir / 'sumo_tripinfo.xml'}", file=sys.stderr)

    adaptive = {**a_trip, **a_summ}
    baseline_data = {**b_trip, **b_summ}

    # Read baseline timing from run_summary if available
    summary_path = baseline_dir / "run_summary.json"
    green_s, yellow_s = 30, 5
    if summary_path.exists():
        with open(summary_path) as f:
            s = json.load(f)
        step_len = 0.05
        green_s  = round(s.get("green_steps",  600) * step_len)
        yellow_s = round(s.get("yellow_steps", 100) * step_len)
    baseline_data["green_s"]  = green_s
    baseline_data["yellow_s"] = yellow_s

    print_report(adaptive, baseline_data, scenario=args.scenario or "")


if __name__ == "__main__":
    main()
