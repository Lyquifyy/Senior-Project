from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit
import threading
import json
import os
import time
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()
app.config["SECRET_KEY"] = os.getenv("SEC_KEY")
socketio = SocketIO(app, cors_allowed_origins="*")

# ---------------------------------------------------------------------------
# Emission data path resolution
# ---------------------------------------------------------------------------
# Set SUMO_EMISSION_DATA_PATH to override.
# Defaults to SUMO/Carbon-Emission-Traffic/emissionData for network-format,
# or SUMO/Rev-5/emissionData for the legacy single-intersection format.
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.normpath(os.path.join(_script_dir, ".."))
_raw_emission = os.getenv("SUMO_EMISSION_DATA_PATH")
if _raw_emission and os.path.isabs(_raw_emission):
    EMISSION_DATA_PATH = os.path.normpath(_raw_emission)
elif _raw_emission:
    EMISSION_DATA_PATH = os.path.normpath(os.path.join(_project_root, _raw_emission))
else:
    EMISSION_DATA_PATH = os.path.normpath(
        os.path.join(_project_root, "SUMO", "Carbon-Emission-Traffic", "emissionData")
    )

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
latest_data = {
    "step": 0,
    "intersection": "238",
    "lanes": {},
    "co2": 0,
    "avg_wait_time": 0,
    "cars": {"north": 0, "south": 0, "east": 0, "west": 0},
    "lights": {"north": "red", "south": "red", "east": "red", "west": "red"},
    "total_vehicles": 0,
    # Network-format extras (empty when reading legacy files)
    "network_queue": 0,
    "intersections": {},
    "format": "legacy",
}
data_lock = threading.Lock()

# Lane→direction mapping for intersection 238 (Town03)
_LANE_DIRECTION_238 = {
    "north": ["-4_3", "-4_4"],
    "south": ["-69_4", "-69_3"],
    "east":  ["-24_3", "24_4"],
    "west":  ["-23_4", "23_3"],
}


# ---------------------------------------------------------------------------
# Data processors
# ---------------------------------------------------------------------------

def process_legacy_data(raw: dict) -> dict:
    """Process the old single-intersection lane_emissions_step_*.json format."""
    lanes = raw.get("lanes", {})

    total_co2 = 0.0
    total_speed = 0.0
    vehicle_count = 0
    stopped = 0

    for lane_vehicles in lanes.values():
        for car in lane_vehicles:
            vehicle_count += 1
            try:
                total_co2 += float(car.get("co2", 0))
            except (ValueError, TypeError):
                pass
            speed = car.get("speed", 0)
            total_speed += speed
            if speed < 0.5:
                stopped += 1

    avg_wait = (stopped / max(vehicle_count, 1)) * 2.5

    cars = {
        direction: sum(len(lanes.get(lane, [])) for lane in lane_list)
        for direction, lane_list in _LANE_DIRECTION_238.items()
    }

    return {
        "step": raw.get("step", 0),
        # Legacy CO2 values from getCO2Emission are in mg/s; divide by 1,000,000 → kg
        "co2": round(total_co2 / 1_000_000, 4),
        "avg_wait_time": round(avg_wait, 2),
        "cars": cars,
        "lights": raw.get("lights", latest_data["lights"]),
        "total_vehicles": vehicle_count,
        "network_queue": stopped,
        "intersections": {
            raw.get("intersection", "238"): {
                "co2": round(total_co2 / 1_000_000, 4),
                "vehicles": vehicle_count,
                "queue": stopped,
            }
        },
        "format": "legacy",
    }


def process_network_data(raw: dict) -> dict:
    """Process the network-wide network_step_*.json format (Carbon-Emission-Traffic)."""
    totals = raw.get("network_totals", {})
    tls_data = raw.get("tls", {})

    total_co2_kg = totals.get("co2_kg", 0.0)
    total_vehicles = totals.get("vehicles", 0)
    total_queue = totals.get("queue", 0)
    total_wait = totals.get("total_waiting_time", 0.0)

    avg_wait = (total_queue / max(total_vehicles, 1)) * 2.5

    # Per-intersection summary
    intersections = {
        tls_id: {
            "co2": tls.get("co2_kg", round(tls.get("co2_mg", 0) / 1_000_000, 6)),
            "vehicles": tls.get("vehicle_count", 0),
            "queue": tls.get("queue", 0),
            "current_phase": tls.get("current_phase", 0),
            "chosen_phase": tls.get("chosen_phase", 0),
            "switched": tls.get("switched", False),
        }
        for tls_id, tls in tls_data.items()
    }

    # Directional car counts for TLS 238 specifically (for dashboard cards)
    lanes_238 = tls_data.get("238", {}).get("lanes", {})
    cars = {
        direction: sum(len(lanes_238.get(lane, [])) for lane in lane_list)
        for direction, lane_list in _LANE_DIRECTION_238.items()
    }

    return {
        "step": raw.get("step", 0),
        "co2": round(total_co2_kg, 4),
        "avg_wait_time": round(avg_wait, 2),
        "cars": cars,
        "lights": latest_data["lights"],  # unchanged until CARLA provides phase→colour map
        "total_vehicles": total_vehicles,
        "network_queue": total_queue,
        "intersections": intersections,
        "format": "network",
    }


# ---------------------------------------------------------------------------
# File watcher
# ---------------------------------------------------------------------------

def _find_latest_file(directory: str):
    """Return (filepath, step_num, is_network_format) for the newest snapshot file.

    Prefers network_step_*.json over lane_emissions_step_*.json when both exist.
    Returns None if no files found.
    """
    try:
        all_files = os.listdir(directory)
    except FileNotFoundError:
        return None

    def _step(name: str, prefix: str) -> int:
        try:
            return int(name[len(prefix):].replace(".json", ""))
        except ValueError:
            return -1

    network_files = sorted(
        [f for f in all_files if f.startswith("network_step_") and f.endswith(".json")],
        key=lambda f: _step(f, "network_step_"),
    )
    legacy_files = sorted(
        [f for f in all_files if f.startswith("lane_emissions_step_") and f.endswith(".json")],
        key=lambda f: _step(f, "lane_emissions_step_"),
    )

    if network_files:
        fname = network_files[-1]
        return os.path.join(directory, fname), _step(fname, "network_step_"), True
    if legacy_files:
        fname = legacy_files[-1]
        return os.path.join(directory, fname), _step(fname, "lane_emissions_step_"), False
    return None


def watch_emission_files():
    """Monitor emissionData directory for new snapshot files and push to clients."""
    last_step = -1
    print(f"Watching for simulation files in: {os.path.abspath(EMISSION_DATA_PATH)}")

    while True:
        try:
            if not os.path.exists(EMISSION_DATA_PATH):
                print(f"Waiting for {EMISSION_DATA_PATH} to be created…")
                time.sleep(2)
                continue

            result = _find_latest_file(EMISSION_DATA_PATH)
            if result is None:
                time.sleep(1)
                continue

            file_path, step_num, is_network = result
            if step_num > last_step:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                processed = (
                    process_network_data(raw_data)
                    if is_network
                    else process_legacy_data(raw_data)
                )

                with data_lock:
                    latest_data.update(processed)

                socketio.emit("simulation_update", processed)
                last_step = step_num
                fmt = "network" if is_network else "legacy"
                print(f"[{fmt}] Broadcasted step {step_num} to dashboard")

        except Exception as exc:
            print(f"Error watching files: {exc}")

        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Flask routes and SocketIO events
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/get", methods=["GET"])
def get_data():
    """REST endpoint returning the latest processed snapshot."""
    with data_lock:
        return jsonify(latest_data)


@app.route("/summary", methods=["GET"])
def get_summary():
    """Return the run_summary.json from the log directory if available."""
    log_dir = os.path.normpath(
        os.path.join(EMISSION_DATA_PATH, "..", "logs")
    )
    summary_path = os.path.join(log_dir, "run_summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"error": "run_summary.json not found"}), 404


@socketio.on("connect")
def handle_connect():
    print("Client connected")
    with data_lock:
        emit("simulation_update", latest_data)


@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("SMART INTERSECTION DASHBOARD SERVER")
    print("=" * 60)
    print(f"Emission data path : {os.path.abspath(EMISSION_DATA_PATH)}")
    print(f"Supported formats  : network_step_*.json, lane_emissions_step_*.json")
    print(f"Server             : http://localhost:5000")
    print("=" * 60)

    watcher_thread = threading.Thread(target=watch_emission_files, daemon=True)
    watcher_thread.start()

    socketio.run(app, debug=True, use_reloader=False, host="0.0.0.0", port=5000)
