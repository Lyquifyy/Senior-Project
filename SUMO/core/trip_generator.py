"""Central trip generation for SUMO. All paths are explicit or under output_dir."""

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


def sanitize_id(raw_id: str, index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-\.]", "_", raw_id)
    if not safe or not safe[0].isalpha():
        safe = f"veh_{safe}"
    return f"{safe}_{index}"


def generate_vtypes(csv_file: Union[Path, str], vtypes_file: Union[Path, str]) -> None:
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
