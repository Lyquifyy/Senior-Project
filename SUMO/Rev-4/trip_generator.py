"""Trip generation helpers for the SUMO Rev-4 scenario.

Provides small utilities to generate vehicle types from a CSV, create random
trips using the included randomTrips.py, assign vtypes by CO2 buckets, and
convert trips into routes using duarouter. The public function
``generate_trips`` is used by the traffic control script and keeps the same
signature/return values (rou_file, vtypes_file).
"""

from pathlib import Path
import csv
import random
import subprocess
import xml.etree.ElementTree as ET
import re
import logging
import sys
from typing import Tuple, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent

# Default input/output file names (resolved to BASE_DIR when used)
DEFAULT_CSV = BASE_DIR / "carData.csv"
DEFAULT_VTYPES = BASE_DIR / "vtypes.xml"
DEFAULT_TRIPS = BASE_DIR / "random.trips.xml"
DEFAULT_CUSTOM_TRIPS = BASE_DIR / "custom.trips.xml"
DEFAULT_ROUTES = BASE_DIR / "custom.rou.xml"
DEFAULT_NET = BASE_DIR / "net.net.xml"


def sanitize_id(raw_id: str, index: int) -> str:
    """Create a safe XML id from an arbitrary raw id.

    Non-alphanumeric characters are replaced with underscores and an index is
    appended to ensure uniqueness.
    """
    safe = re.sub(r"[^A-Za-z0-9_\-\.]", "_", raw_id)
    if not safe or not safe[0].isalpha():
        safe = f"veh_{safe}"
    return f"{safe}_{index}"


def generate_vtypes(csv_file: Path | str, vtypes_file: Path | str) -> None:
    """Generate a vTypeDistribution XML file from a CSV of vehicle data.

    The CSV is expected to contain columns like 'Test Vehicle ID', 'emmissionClass',
    'maxSpeed', 'length', and 'CO2 (g/mi)'. Missing fields get sensible defaults.
    """
    csv_path = Path(csv_file)
    vtypes_path = Path(vtypes_file)

    vtypes = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            vtype_id = sanitize_id(row.get("Test Vehicle ID", "car"), i)
            emission = row.get("emmissionClass", "HBEFA3/PC_G_EU4")
            max_speed = row.get("maxSpeed", "33")
            length = row.get("length", "5.0")
            co2 = row.get("CO2 (g/mi)", "0")

            vtypes.append(
                f'<vType id="{vtype_id}" vClass="passenger" '
                f'emissionClass="{emission}" maxSpeed="{max_speed}" length="{length}">\n'
                f'  <param key="customCO2" value="{co2}"/>\n'
                f'</vType>'
            )

    vtypes_path.parent.mkdir(parents=True, exist_ok=True)
    with vtypes_path.open("w", encoding="utf-8") as f:
        f.write('<vTypeDistribution id="customTypes">\n')
        for v in vtypes:
            f.write("  " + v + "\n")
        f.write("</vTypeDistribution>\n")


def run_random_trips(net_file: Path | str, trips_file: Path | str, sim_end: int) -> None:
    """Run the included randomTrips.py to produce a trips XML file.

    Uses the Python interpreter running this script to invoke the tool.
    """
    net_path = Path(net_file)
    trips_path = Path(trips_file)
    random_trips_script = BASE_DIR / "randomTrips.py"

    if not random_trips_script.exists():
        raise FileNotFoundError(f"randomTrips.py not found at {random_trips_script}")

    cmd = [sys.executable, str(random_trips_script), "-n", str(net_path), "-e", str(sim_end), "-o", str(trips_path), "--seed", "42"]
    logger.info("Running randomTrips: %s", cmd)
    subprocess.run(cmd, check=True)

    if not trips_path.exists():
        raise FileNotFoundError(f"Trips file {trips_path} not found after running randomTrips.")


def assign_vtypes(trips_file: Path | str, vtypes_file: Path | str, custom_trips_file: Path | str,
                  heavyCO2Percent: Optional[float], threshold: float) -> None:
    """Assign vtypes to trips based on CO2 buckets and write a new trips file."""
    vtypes_path = Path(vtypes_file)
    trips_path = Path(trips_file)
    custom_path = Path(custom_trips_file)

    try:
        tree = ET.parse(vtypes_path)
    except ET.ParseError as e:
        raise RuntimeError(f"Failed to parse vtypes file {vtypes_path}: {e}")

    root = tree.getroot()
    heavy_vtypes = []
    light_vtypes = []

    for v in root.findall("vType"):
        vtype_id = v.attrib.get("id")
        custom_co2 = 0.0
        for param in v.findall("param"):
            if param.attrib.get("key") == "customCO2":
                try:
                    custom_co2 = float(param.attrib.get("value", 0.0))
                except ValueError:
                    custom_co2 = 0.0
        if vtype_id:
            if custom_co2 >= threshold:
                heavy_vtypes.append(vtype_id)
            else:
                light_vtypes.append(vtype_id)

    try:
        trips_tree = ET.parse(trips_path)
    except ET.ParseError as e:
        raise RuntimeError(f"Failed to parse trips file {trips_path}: {e}")

    trips_root = trips_tree.getroot()
    trips = trips_root.findall("trip")

    all_vtypes = heavy_vtypes + light_vtypes
    if not all_vtypes:
        raise RuntimeError("No vtypes available to assign to trips.")

    if heavyCO2Percent and heavy_vtypes and light_vtypes:
        total_trips = len(trips)
        num_heavy = int(total_trips * heavyCO2Percent)
        heavy_indices = set(random.sample(range(total_trips), min(num_heavy, total_trips)))
        for i, trip in enumerate(trips):
            if i in heavy_indices:
                trip.set("type", random.choice(heavy_vtypes))
            else:
                trip.set("type", random.choice(light_vtypes))
    else:
        for trip in trips:
            trip.set("type", random.choice(all_vtypes))

    custom_path.parent.mkdir(parents=True, exist_ok=True)
    trips_tree.write(custom_path)


def run_duarouter(net_file: Path | str, custom_trips_file: Path | str, routes_file: Path | str, vtypes_file: Path | str) -> None:
    """Run duarouter to convert trips into routes. Exits on non-zero status."""
    cmd = [
        "duarouter",
        "-n", str(net_file),
        "-t", str(custom_trips_file),
        "-o", str(routes_file),
        "--additional-files", str(vtypes_file),
    ]
    logger.info("Running duarouter: %s", cmd)
    subprocess.run(cmd, check=True)


def generate_trips(csv_file: str | Path, net_file: str | Path, sim_end: int = 1000,
                   heavyCO2Percent: Optional[float] = None, threshold: float = 250.0) -> Tuple[str, str]:
    """High-level helper that runs all steps and returns (rou_file, vtypes_file).

    Returns paths as strings to match the previous behavior.
    """
    csv_path = Path(csv_file)
    net_path = Path(net_file)

    logger.info("Starting trip generator")
    logger.info("Generating vtypes.xml from %s", csv_path)
    generate_vtypes(csv_path, DEFAULT_VTYPES)

    logger.info("Running randomTrips.py to produce %s", DEFAULT_TRIPS)
    run_random_trips(net_path, DEFAULT_TRIPS, sim_end)

    logger.info("Assigning custom vtypes (heavy percent: %s)", heavyCO2Percent)
    assign_vtypes(DEFAULT_TRIPS, DEFAULT_VTYPES, DEFAULT_CUSTOM_TRIPS, heavyCO2Percent, threshold)

    logger.info("Running duarouter to produce routes file %s", DEFAULT_ROUTES)
    run_duarouter(net_path, DEFAULT_CUSTOM_TRIPS, DEFAULT_ROUTES, DEFAULT_VTYPES)

    logger.info("Trip generation finished")
    return str(DEFAULT_ROUTES), str(DEFAULT_VTYPES)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate SUMO trips and routes from CSV data")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="CSV file with vehicle data")
    parser.add_argument("--net", default=str(DEFAULT_NET), help="SUMO net file")
    parser.add_argument("--sim-end", type=int, default=1000, help="End time for randomTrips")
    parser.add_argument("--heavy-co2", type=float, default=None, help="Fraction of trips to mark heavy CO2")
    parser.add_argument("--threshold", type=float, default=250.0, help="CO2 threshold for heavy vtypes")
    args = parser.parse_args()

    generate_trips(args.csv, args.net, sim_end=args.sim_end, heavyCO2Percent=args.heavy_co2, threshold=args.threshold)


