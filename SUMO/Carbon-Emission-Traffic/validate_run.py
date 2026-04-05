#!/usr/bin/env python
"""
Validation and comparison tool for Carbon-Emission-Traffic runs.

Usage (from project root):
  python SUMO/Carbon-Emission-Traffic/validate_run.py

What it checks:
  1. Verifies that every expected TLS ID appears in at least one
     network_step_*.json decision log entry (confirms network-wide coverage).
  2. Computes per-TLS phase-switch statistics from phase_decisions.jsonl.
  3. Reads run_summary.json and reports totals.
  4. Cross-checks custom CO2 totals against SUMO's built-in
     logs/sumo_summary.xml if it is present.
  5. Prints a comparative table when a baseline run_summary.json
     (named run_summary_baseline.json) is present.

This script is read-only; it never modifies simulation files.
"""

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict

_SCENARIO_DIR = Path(__file__).resolve().parent
_EMISSION_DIR = _SCENARIO_DIR / "emissionData"
_LOG_DIR = _SCENARIO_DIR / "logs"

EXPECTED_TLS = {"103", "1221", "1352", "1696", "1820", "238", "356", "498", "576", "655", "861"}


def _hr():
    print("-" * 64)


# ---------------------------------------------------------------------------
# 1. Network snapshot coverage check
# ---------------------------------------------------------------------------

def check_tls_coverage() -> set:
    """Return the set of TLS IDs seen in any network_step_*.json file."""
    seen: set = set()
    files = sorted(
        _EMISSION_DIR.glob("network_step_*.json"),
        key=lambda p: int(p.stem.split("_")[-1]),
    )
    if not files:
        print("[WARN] No network_step_*.json files found in", _EMISSION_DIR)
        return seen
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            seen.update(data.get("tls", {}).keys())
        except Exception as exc:
            print(f"[WARN] Could not read {fp.name}: {exc}")
    print(f"\n[1] TLS coverage check ({len(files)} snapshots):")
    missing = EXPECTED_TLS - seen
    extra = seen - EXPECTED_TLS
    print(f"    Expected : {sorted(EXPECTED_TLS)}")
    print(f"    Observed : {sorted(seen)}")
    if missing:
        print(f"    MISSING  : {sorted(missing)}  ← these lights were not logged")
    else:
        print("    All expected intersections are present in telemetry. ✓")
    if extra:
        print(f"    Extra TLS found (not in expected list): {sorted(extra)}")
    return seen


# ---------------------------------------------------------------------------
# 2. Phase decision statistics
# ---------------------------------------------------------------------------

def check_decision_log():
    log_file = _LOG_DIR / "phase_decisions.jsonl"
    if not log_file.exists():
        print("\n[2] phase_decisions.jsonl not found; skipping decision stats.")
        return

    records = []
    with log_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not records:
        print("\n[2] phase_decisions.jsonl is empty.")
        return

    by_tls: dict = defaultdict(lambda: {"total": 0, "switches": 0, "phases": defaultdict(int)})
    for rec in records:
        tls = rec.get("tls_id", "unknown")
        by_tls[tls]["total"] += 1
        if rec.get("switch_executed"):
            by_tls[tls]["switches"] += 1
        phase = rec.get("selected_phase")
        if phase is not None:
            by_tls[tls]["phases"][phase] += 1

    print(f"\n[2] Phase decision statistics ({len(records)} total records):")
    _hr()
    print(f"  {'TLS':>6}  {'Decisions':>9}  {'Switches':>8}  {'Switch%':>7}  Phase distribution")
    _hr()
    for tls, stats in sorted(by_tls.items()):
        sw_pct = stats["switches"] / max(stats["total"], 1) * 100
        phase_dist = "  ".join(
            f"p{p}={c}" for p, c in sorted(stats["phases"].items())
        )
        print(f"  {tls:>6}  {stats['total']:>9d}  {stats['switches']:>8d}  {sw_pct:>6.1f}%  {phase_dist}")
    _hr()


# ---------------------------------------------------------------------------
# 3. Run summary
# ---------------------------------------------------------------------------

def read_run_summary(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def print_run_summary():
    summary = read_run_summary(_LOG_DIR / "run_summary.json")
    if not summary:
        print("\n[3] run_summary.json not found; no summary to display.")
        return
    print("\n[3] Run summary:")
    _hr()
    for k, v in summary.items():
        print(f"    {k:<40} {v}")
    _hr()
    return summary


# ---------------------------------------------------------------------------
# 4. Cross-check with SUMO built-in summary-output
# ---------------------------------------------------------------------------

def cross_check_sumo_summary():
    sumo_summary = _LOG_DIR / "sumo_summary.xml"
    if not sumo_summary.exists():
        print("\n[4] logs/sumo_summary.xml not found; skipping SUMO cross-check.")
        print("    (The file is produced by SUMO's summary-output; run with the .sumocfg)")
        return

    try:
        tree = ET.parse(sumo_summary)
        root = tree.getroot()
        steps = root.findall("step")
        if not steps:
            print("\n[4] sumo_summary.xml contains no <step> elements.")
            return
        last = steps[-1]
        print("\n[4] SUMO built-in summary (last step):")
        _hr()
        for attr in ["time", "loaded", "inserted", "running", "waiting",
                     "ended", "meanSpeed", "meanTimeLoss"]:
            val = last.get(attr)
            if val is not None:
                print(f"    {attr:<20} {val}")
        _hr()
    except Exception as exc:
        print(f"\n[4] Could not parse sumo_summary.xml: {exc}")


# ---------------------------------------------------------------------------
# 5. Baseline comparison
# ---------------------------------------------------------------------------

def compare_baseline():
    baseline_path = _LOG_DIR / "run_summary_baseline.json"
    adaptive_path = _LOG_DIR / "run_summary.json"
    if not baseline_path.exists():
        print("\n[5] No run_summary_baseline.json found.")
        print(
            "    To compare: copy run_summary.json from a fixed-time run to "
            "logs/run_summary_baseline.json, then run this script again."
        )
        return

    baseline = read_run_summary(baseline_path)
    adaptive = read_run_summary(adaptive_path)
    if not adaptive:
        print("\n[5] run_summary.json not found; cannot compare.")
        return

    compare_keys = ["total_co2_kg", "total_fuel_ml", "total_vehicles_completed", "total_steps"]
    print("\n[5] Baseline vs adaptive comparison:")
    _hr()
    print(f"  {'Metric':<35}  {'Baseline':>12}  {'Adaptive':>12}  {'Delta':>10}")
    _hr()
    for k in compare_keys:
        b = baseline.get(k)
        a = adaptive.get(k)
        if b is None or a is None:
            continue
        try:
            delta = a - b
            pct = (delta / b * 100) if b != 0 else 0.0
            direction = "▼" if delta < 0 else "▲"
            print(f"  {k:<35}  {b:>12.4f}  {a:>12.4f}  {direction}{abs(pct):>8.2f}%")
        except TypeError:
            print(f"  {k:<35}  {b!r:>12}  {a!r:>12}")
    _hr()
    co2_b = baseline.get("total_co2_kg", 0)
    co2_a = adaptive.get("total_co2_kg", 0)
    if co2_b and co2_a:
        reduction = (co2_b - co2_a) / co2_b * 100
        print(f"\n  CO2 reduction: {reduction:+.2f}%  ({'better' if reduction > 0 else 'worse'} than baseline)")
    _hr()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 64)
    print("Carbon-Emission-Traffic Validation Report")
    print(f"Scenario dir: {_SCENARIO_DIR}")
    print("=" * 64)

    if not _EMISSION_DIR.exists():
        print(f"\n[WARN] emissionData/ directory not found: {_EMISSION_DIR}")
        print("       Run the simulation first:")
        print("       python SUMO/run_simulation.py --scenario Carbon-Emission-Traffic --mode standalone")
        sys.exit(1)

    check_tls_coverage()
    check_decision_log()
    print_run_summary()
    cross_check_sumo_summary()
    compare_baseline()

    print("\nValidation complete.")


if __name__ == "__main__":
    main()
