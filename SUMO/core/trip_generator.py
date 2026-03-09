"""Central trip generation for SUMO. All paths are explicit or under output_dir.

Vehicle type generation now maps the CSV's Vehicle Type column and CO2 (g/mi)
value to more differentiated SUMO emission classes (HBEFA3 family), while
keeping customCO2 as a reference parameter for scenario metadata and the
Carbon-Emission-Traffic carbon-aware heuristic.

Emission class tiers used (HBEFA3 classes, validated in SUMO ≥ 1.0):
  Passenger cars:
    < 200 g/mi  → PC_G_EU6   (modern, efficient petrol)
    200-260 g/mi → PC_G_EU4  (mid-range petrol, current default)
    260-320 g/mi → PC_G_EU2  (older petrol)
    > 320 g/mi  → PC_G_EU0   (pre-Euro, high emitter)
  Trucks / LCV:
    any CO2     → LDV_D_EU4  (light diesel delivery)
  Unknown / Both:
    falls back to car tier by CO2 value
"""

from pathlib import Path
import csv
import random
import subprocess
import xml.etree.ElementTree as ET
import re
import logging
import sys
from typing import Tuple, Optional, Union

logger = logging.getLogger(__name__)

_CORE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Emission class mapping
# ---------------------------------------------------------------------------

# CO2 thresholds in g/mi (US EPA) mapped to HBEFA3 emission classes.
# Thresholds are inclusive-lower, exclusive-upper.
_CAR_CO2_TIERS: list = [
    (200.0, "HBEFA3/PC_G_EU6"),   # < 200 g/mi — modern, efficient
    (260.0, "HBEFA3/PC_G_EU4"),   # 200-260 g/mi — average modern
    (320.0, "HBEFA3/PC_G_EU2"),   # 260-320 g/mi — older petrol
]
_CAR_CO2_FALLBACK = "HBEFA3/PC_G_EU0"          # > 320 g/mi — pre-Euro / very polluting
_TRUCK_EMISSION_CLASS = "HBEFA3/LDV_D_EU4"     # light diesel delivery for trucks

# vClass and default length derived from CSV Vehicle Type column
_VTYPE_ATTRS = {
    "Car":   {"vClass": "passenger", "length": "4.5"},
    "Truck": {"vClass": "delivery",  "length": "6.5"},
    "Both":  {"vClass": "passenger", "length": "4.5"},  # treat mixed as passenger
}
_VTYPE_ATTRS_DEFAULT = {"vClass": "passenger", "length": "5.0"}


def _emission_class_from_co2(co2_g_mi: float, vehicle_type: str) -> str:
    """Return the most appropriate SUMO emission class for the given CSV row."""
    vtype_upper = str(vehicle_type).strip().title()
    if vtype_upper == "Truck":
        return _TRUCK_EMISSION_CLASS
    for threshold, cls in _CAR_CO2_TIERS:
        if co2_g_mi < threshold:
            return cls
    return _CAR_CO2_FALLBACK


# ---------------------------------------------------------------------------
# ID sanitizer
# ---------------------------------------------------------------------------

def sanitize_id(raw_id: str, index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-\.]", "_", raw_id)
    if not safe or not safe[0].isalpha():
        safe = f"veh_{safe}"
    return f"{safe}_{index}"


# ---------------------------------------------------------------------------
# Core generation steps
# ---------------------------------------------------------------------------

def generate_vtypes(csv_file: Union[Path, str], vtypes_file: Union[Path, str]) -> None:
    """Generate vtypes.xml from cars.csv.

    Each vType now carries:
      - emissionClass derived from CO2 value and vehicle category
      - vClass derived from Vehicle Type column (Car / Truck / Both)
      - length derived from Vehicle Type column
      - customCO2 parameter (original g/mi value for metadata / legacy scoring)
    """
    csv_path = Path(csv_file)
    vtypes_path = Path(vtypes_file)
    vtypes = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            vtype_id = sanitize_id(row.get("Test Vehicle ID", "car"), i)
            vehicle_type = row.get("Vehicle Type", "Car").strip()
            attrs = _VTYPE_ATTRS.get(vehicle_type.title(), _VTYPE_ATTRS_DEFAULT)

            raw_co2_str = row.get("CO2 (g/mi)", "0")
            try:
                co2_g_mi = float(raw_co2_str)
            except (ValueError, TypeError):
                co2_g_mi = 0.0

            # Derive SUMO emission class from CO2 and vehicle category
            emission_class = _emission_class_from_co2(co2_g_mi, vehicle_type)

            # Allow CSV to override emission class if the column is present and valid
            csv_emission = row.get("emmissionClass", "").strip()
            if csv_emission:
                emission_class = csv_emission

            max_speed = row.get("maxSpeed", "33").strip() or "33"
            length = attrs["length"]
            vclass = attrs["vClass"]

            vtypes.append(
                f'<vType id="{vtype_id}" vClass="{vclass}" '
                f'emissionClass="{emission_class}" maxSpeed="{max_speed}" '
                f'length="{length}">\n'
                f'  <param key="customCO2" value="{co2_g_mi}"/>\n'
                f'  <param key="vehicleCategory" value="{vehicle_type}"/>\n'
                f'</vType>'
            )
    vtypes_path.parent.mkdir(parents=True, exist_ok=True)
    with vtypes_path.open("w", encoding="utf-8") as f:
        f.write('<vTypeDistribution id="customTypes">\n')
        for v in vtypes:
            f.write("  " + v + "\n")
        f.write("</vTypeDistribution>\n")
    logger.info("Generated %d vehicle types in %s", len(vtypes), vtypes_path)


def run_random_trips(
    net_file: Union[Path, str],
    trips_file: Union[Path, str],
    sim_end: int,
    random_trips_script: Optional[Union[Path, str]] = None,
) -> None:
    net_path = Path(net_file)
    trips_path = Path(trips_file)
    script = Path(random_trips_script) if random_trips_script else _CORE_DIR / "randomTrips.py"
    if not script.exists():
        raise FileNotFoundError(f"randomTrips.py not found at {script}")
    cmd = [
        sys.executable, str(script),
        "-n", str(net_path), "-e", str(sim_end), "-o", str(trips_path),
        "--seed", "42",
    ]
    logger.info("Running randomTrips: %s", cmd)
    subprocess.run(cmd, check=True)
    if not trips_path.exists():
        raise FileNotFoundError(f"Trips file {trips_path} not found after randomTrips.")


def assign_vtypes(
    trips_file: Union[Path, str],
    vtypes_file: Union[Path, str],
    custom_trips_file: Union[Path, str],
    heavyCO2Percent: Optional[float],
    threshold: float,
) -> None:
    """Assign vehicle types to trips, splitting by CO2 threshold.

    Heavy vehicles (customCO2 >= threshold g/mi) are assigned to
    heavyCO2Percent of all trips; the rest receive light vehicles.
    """
    vtypes_path = Path(vtypes_file)
    trips_path = Path(trips_file)
    custom_path = Path(custom_trips_file)
    tree = ET.parse(vtypes_path)
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
                    pass
        if vtype_id:
            if custom_co2 >= threshold:
                heavy_vtypes.append(vtype_id)
            else:
                light_vtypes.append(vtype_id)
    trips_tree = ET.parse(trips_path)
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
    logger.info("Assigned vehicle types to %d trips", len(trips))


def run_duarouter(
    net_file: Union[Path, str],
    custom_trips_file: Union[Path, str],
    routes_file: Union[Path, str],
    vtypes_file: Union[Path, str],
) -> None:
    cmd = [
        "duarouter",
        "-n", str(net_file), "-t", str(custom_trips_file),
        "-o", str(routes_file), "--additional-files", str(vtypes_file),
        "--ignore-errors",
    ]
    logger.info("Running duarouter: %s", cmd)
    subprocess.run(cmd, check=True)


def generate_trips(
    csv_file: Union[str, Path],
    net_file: Union[str, Path],
    sim_end: int = 1000,
    heavyCO2Percent: Optional[float] = None,
    threshold: float = 250.0,
    output_dir: Optional[Union[str, Path]] = None,
    random_trips_script: Optional[Union[str, Path]] = None,
) -> Tuple[str, str]:
    """Generate trips and routes. If output_dir is None, uses parent of net_file."""
    csv_path = Path(csv_file)
    net_path = Path(net_file)
    out = Path(output_dir) if output_dir else net_path.parent
    out.mkdir(parents=True, exist_ok=True)
    trips_file = out / "random.trips.xml"
    custom_trips_file = out / "custom.trips.xml"
    routes_file = out / "custom.rou.xml"
    vtypes_file = out / "vtypes.xml"

    logger.info("Starting trip generator (output_dir=%s)", out)
    generate_vtypes(csv_path, vtypes_file)
    run_random_trips(net_path, trips_file, sim_end, random_trips_script)
    assign_vtypes(trips_file, vtypes_file, custom_trips_file, heavyCO2Percent, threshold)
    run_duarouter(net_path, custom_trips_file, routes_file, vtypes_file)
    logger.info("Trip generation finished")
    return str(routes_file), str(vtypes_file)
