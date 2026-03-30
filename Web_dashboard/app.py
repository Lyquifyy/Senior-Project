from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit
import threading
import queue
import os
import logging
import traci
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


app = Flask(__name__)
load_dotenv()

secret_key = os.getenv("SEC_KEY")
if secret_key is None:
    raise RuntimeError("SEC_KEY must be set in the environment or .env file.")

app.config["SECRET_KEY"] = secret_key

_cors_env = os.getenv("CORS_ORIGINS", "http://127.0.0.1:5000")
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]

socketio = SocketIO(app, cors_allowed_origins=CORS_ORIGINS)

TARGET_TLS = os.getenv("TARGET_TLS", "238")

LANE_DIRECTION = {
    "-4_3":  "north", "-4_4":  "north",
    "-69_4": "south", "-69_3": "south",
    "-24_3": "east",  "24_4":  "east",
    "-23_4": "west",  "23_3":  "west",
}
PHASE_MAP = {
    "G": "green", "g": "green",
    "Y": "yellow", "y": "yellow",
    "R": "red",    "r": "red",
}
COLOUR_PRIORITY = {"green": 2, "yellow": 1, "red": 0}
sim_queue: queue.Queue = queue.Queue(maxsize=60)

latest_data: dict = {
    "step":           0,
    "intersection":   TARGET_TLS,
    "co2":            0.0,
    "avg_wait_time":  0.0,
    "cars":           {"north": 0, "south": 0, "east": 0, "west": 0},
    "lights":         {"north": "red", "south": "red", "east": "red", "west": "red"},
    "total_vehicles": 0,
}
data_lock = threading.Lock()

# Snapshot builder (reads live TraCI state — no file I/O)
def _build_snapshot() -> dict:
    cars          = {"north": 0, "south": 0, "east": 0, "west": 0}
    total_co2     = 0.0
    total_wait    = 0.0
    vehicle_count = 0

    for lane_id, direction in LANE_DIRECTION.items():
        try:
            veh_ids = traci.lane.getLastStepVehicleIDs(lane_id)
        except traci.exceptions.TraCIException:
            continue
        cars[direction] += len(veh_ids)
        for vid in veh_ids:
            vehicle_count += 1
            try:
                total_co2  += traci.vehicle.getCO2Emission(vid)
                total_wait += traci.vehicle.getWaitingTime(vid)
            except traci.exceptions.TraCIException:
                pass

    lights = {d: "red" for d in ("north", "south", "east", "west")}
    try:
        state = traci.trafficlight.getRedYellowGreenState(TARGET_TLS)
        links = traci.trafficlight.getControlledLinks(TARGET_TLS)
        for i, link_group in enumerate(links):
            if i >= len(state):
                break
            colour = PHASE_MAP.get(state[i], "red")
            for (from_lane, _to, _via) in link_group:
                direction = LANE_DIRECTION.get(from_lane)
                if direction:
                    if COLOUR_PRIORITY.get(colour, 0) > COLOUR_PRIORITY.get(lights[direction], 0):
                        lights[direction] = colour
    except traci.exceptions.TraCIException:
        pass

    return {
        "step":           int(traci.simulation.getTime()),
        "intersection":   TARGET_TLS,
        "co2":            round(total_co2 / 1_000_000, 4),   # TraCI returns mg/s; convert to kg/s
        "avg_wait_time":  round((total_wait / vehicle_count) if vehicle_count else 0.0, 2),
        "cars":           cars,
        "lights":         lights,
        "total_vehicles": vehicle_count,
    }


# Monkey-patch traci.simulationStep
_original_step = traci.simulationStep

def _patched_step(*args, **kwargs):
    """Call the real simulationStep, then silently capture a snapshot."""
    result = _original_step(*args, **kwargs)
    try:
        snapshot = _build_snapshot()
        sim_queue.put_nowait(snapshot)
    except Exception:
        logger.warning("_build_snapshot failed — snapshot dropped", exc_info=True)
    return result

traci.simulationStep = _patched_step   # patch before traffic_control is imported


import sys
from pathlib import Path

# Resolve path relative to this file: Web_dashboard/../SUMO/Rev_5
_TC_PATH = Path(__file__).resolve().parent.parent / "SUMO" / "Rev-5"
if not _TC_PATH.is_dir():
    raise RuntimeError(
        f"traffic_control directory not found: {_TC_PATH}\n"
        "Ensure SUMO/Rev-5/ exists relative to Web_dashboard/app.py"
    )
sys.path.insert(0, str(_TC_PATH))

import traffic_control

# Broadcaster — reads queue and pushes to all WebSocket clients
def broadcast_loop() -> None:
    while True:
        data = sim_queue.get()          # block until at least one snapshot is available
        try:
            while True:                 # drain any queued-up snapshots; keep only the latest
                data = sim_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            with data_lock:
                latest_data.update(data)
            socketio.emit("simulation_update", data)
        except Exception:
            logger.exception("broadcast_loop emit error — continuing")



@app.route("/")
def home():
    return render_template("index.html")


@app.route("/get", methods=["GET"])
def get_data():
    with data_lock:
        return jsonify(latest_data)


@socketio.on("connect")
def handle_connect():
    logger.info("Client connected")
    with data_lock:
        emit("simulation_update", latest_data)


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("Client disconnected")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("SMART INTERSECTION DASHBOARD")
    logger.info("Dashboard : http://localhost:5000")
    logger.info("=" * 60)

    threading.Thread(target=broadcast_loop, daemon=True).start()

    threading.Thread(
        target=traffic_control.run_standalone,
        daemon=True,
    ).start()

    socketio.run(app, debug=False, use_reloader=False, host="0.0.0.0", port=5000)