from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit
import threading
import json
import os
import time
from dotenv import load_dotenv, dotenv_values 

app = Flask(__name__)
load_dotenv()
app.config['SECRET_KEY'] = os.getenv("SEC_KEY")
socketio = SocketIO(app, cors_allowed_origins="*")

# Path to SUMO emission data: set SUMO_EMISSION_DATA_PATH to the scenario's emissionData dir
# Example: SUMO_EMISSION_DATA_PATH=SUMO/Rev-4/emissionData or an absolute path
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.normpath(os.path.join(_script_dir, '..'))
_raw_emission = os.getenv('SUMO_EMISSION_DATA_PATH')
if _raw_emission and os.path.isabs(_raw_emission):
    EMISSION_DATA_PATH = os.path.normpath(_raw_emission)
elif _raw_emission:
    EMISSION_DATA_PATH = os.path.normpath(os.path.join(_project_root, _raw_emission))
else:
    EMISSION_DATA_PATH = os.path.normpath(os.path.join(_project_root, 'SUMO', 'Rev-5', 'emissionData'))

#Variable to store latest simulation data
latest_data = {
    "step": 0,
    "intersection": "238",
    "lanes": {},
    "co2": 0,
    "avg_wait_time": 0,
    "cars": {"north": 0, "south": 0, "east": 0, "west": 0},
    "lights": {"north": "red", "south": "red", "east": "red", "west": "red"}
}

# Thread-safe lock
data_lock = threading.Lock()


# Map SUMO lane IDs -> compass direction. Single source of truth for all
# per-direction aggregations below.
LANE_MAPPING = {
    "north": ["-4_3", "-4_4"],
    "south": ["-69_4", "-69_3"],
    "east":  ["-24_3", "24_4"],
    "west":  ["-23_4", "23_3"],
}

# Heavy/light classification threshold (g/mi), matches trip_generator.assign_vtypes.
HEAVY_CO2_THRESHOLD = 250.0


def _direction_for_lane(lane_id):
    for direction, lanes in LANE_MAPPING.items():
        if lane_id in lanes:
            return direction
    return None


def process_simulation_data(sim_data):
    """Convert raw emission snapshot into the dashboard payload.

    Keeps every legacy key (step, co2, avg_wait_time, cars, lights, total_vehicles)
    so the existing UI continues to work, and adds emissions_by_direction,
    emissions_by_class, and algorithm blocks for the new widgets.
    """
    lanes = sim_data.get("lanes", {}) or {}

    # Per-direction accumulators
    by_dir = {
        d: {"co2": 0.0, "nox": 0.0, "fuel": 0.0, "queue": 0, "vehicles": 0}
        for d in LANE_MAPPING
    }
    # Per-class accumulators (heavy vs light)
    by_class = {
        "heavy": {"co2": 0.0, "vehicles": 0},
        "light": {"co2": 0.0, "vehicles": 0},
    }

    total_co2 = 0.0
    vehicle_count = 0
    stopped_vehicles = 0

    for lane_id, vehicles in lanes.items():
        direction = _direction_for_lane(lane_id)
        for car in vehicles:
            vehicle_count += 1
            co2 = float(car.get("co2") or 0.0)
            nox = float(car.get("nox") or 0.0)
            fuel = float(car.get("fuel") or 0.0)
            speed = float(car.get("speed") or 0.0)
            total_co2 += co2
            if speed < 0.5:
                stopped_vehicles += 1
            if direction is not None:
                bucket = by_dir[direction]
                bucket["co2"] += co2
                bucket["nox"] += nox
                bucket["fuel"] += fuel
                bucket["vehicles"] += 1
                if speed < 0.1:
                    bucket["queue"] += 1

            # Heavy vs light classification via customCO2
            custom_co2 = car.get("customCO2")
            if custom_co2 is not None:
                try:
                    cls = "heavy" if float(custom_co2) >= HEAVY_CO2_THRESHOLD else "light"
                    by_class[cls]["co2"] += co2
                    by_class[cls]["vehicles"] += 1
                except (TypeError, ValueError):
                    pass

    avg_wait_time = (stopped_vehicles / max(vehicle_count, 1)) * 2.5

    # Legacy per-direction vehicle counts (used by the N/S/E/W counters on the map)
    cars = {d: by_dir[d]["vehicles"] for d in LANE_MAPPING}

    # Round floats for display
    for bucket in by_dir.values():
        bucket["co2"] = round(bucket["co2"] / 1000, 3)   # kg
        bucket["nox"] = round(bucket["nox"], 3)           # g/s (per-vehicle stream)
        bucket["fuel"] = round(bucket["fuel"], 3)         # ml/s
    for bucket in by_class.values():
        bucket["co2"] = round(bucket["co2"] / 1000, 3)    # kg

    # Algorithm block — average per-lane scores into per-direction scores
    algo_block = sim_data.get("algorithm") or {}
    raw_scores = algo_block.get("scores") or {}
    scores_by_direction = {}
    for direction, lane_ids in LANE_MAPPING.items():
        vals = [float(raw_scores[l]) for l in lane_ids if l in raw_scores]
        scores_by_direction[direction] = round(sum(vals) / len(vals), 2) if vals else 0.0

    algorithm = {
        "current_phase": sim_data.get("current_phase"),
        "chosen_phase": algo_block.get("chosen_phase"),
        "last_decision_step": algo_block.get("last_decision_step"),
        "scores_by_direction": scores_by_direction,
    }

    return {
        "step": sim_data.get("step", 0),
        "co2": round(total_co2 / 1000, 2),  # kg, legacy aggregate
        "avg_wait_time": round(avg_wait_time, 2),
        "cars": cars,
        "lights": sim_data.get("lights", latest_data["lights"]),
        "total_vehicles": vehicle_count,
        "emissions_by_direction": by_dir,
        "emissions_by_class": by_class,
        "algorithm": algorithm,
    }


def watch_emission_files():
    """Monitor emissionData directory for new files (path from SUMO_EMISSION_DATA_PATH or default)."""
    last_step = -1
    print(f"Watching for emission files in: {os.path.abspath(EMISSION_DATA_PATH)}")
    
    while True:
        try:
            if not os.path.exists(EMISSION_DATA_PATH):
                print(f"Waiting for {EMISSION_DATA_PATH} to be created")
                time.sleep(2)
                continue
            
            # Find all emission files
            files = [f for f in os.listdir(EMISSION_DATA_PATH) 
                    if f.startswith("lane_emissions_step_") and f.endswith(".json")]
            
            if not files:
                time.sleep(1)
                continue
            
            # Get the latest file
            files.sort(key=lambda x: int(x.split("_")[-1].replace(".json", "")))
            latest_file = files[-1]
            step_num = int(latest_file.split("_")[-1].replace(".json", ""))
            
            # Only process if it's a new step
            if step_num > last_step:
                file_path = os.path.join(EMISSION_DATA_PATH, latest_file)
                with open(file_path, "r") as f:
                    raw_data = json.load(f)
                
                # Process the data
                processed = process_simulation_data(raw_data)
                
                # Update global data
                with data_lock:
                    latest_data.update(processed)
                
                # Emit to all connected clients
                socketio.emit('simulation_update', processed)
                
                last_step = step_num
                print(f"Broadcasted step {step_num} to dashboard")
        
        except Exception as e:
            print(f"Error watching files: {e}")
        
        time.sleep(0.5)  # Check every 500ms


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/get', methods=['GET'])
def get_data():
    """REST endpoint for current data"""
    with data_lock:
        return jsonify(latest_data)


@socketio.on('connect')
def handle_connect():
    """Send current data when client connects"""
    print('Client connected')
    with data_lock:
        emit('simulation_update', latest_data)


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')


if __name__ == '__main__':
    print("=" * 60)
    print("SMART INTERSECTION DASHBOARD SERVER")
    print("=" * 60)
    print(f"Emission data path: {os.path.abspath(EMISSION_DATA_PATH)}")
    print(f"Server starting at: http://localhost:5000")
    print("=" * 60)
    
    # Start file watcher in background thread
    watcher_thread = threading.Thread(target=watch_emission_files, daemon=True)
    watcher_thread.start()
    
    # Run Flask app with SocketIO
    socketio.run(app, debug=True, use_reloader=False, host='0.0.0.0', port=5000)
