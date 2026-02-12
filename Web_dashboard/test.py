from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit
import threading
import json
import os
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = '*********'
socketio = SocketIO(app, cors_allowed_origins="*")

# Path to SUMO emission data (adjust based on your folder structure)
EMISSION_DATA_PATH = os.path.join('..', 'sumo', 'emissionData')

# Global variable to store latest simulation data
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


def process_simulation_data(sim_data):
    """Convert raw simulation data to dashboard format"""
    lanes = sim_data.get("lanes", {})
    
    # Calculate total fuel consumption
    total_fuel = sum(
        car.get("fuel", 0) for lane in lanes.values() for car in lane
    )
    
    # Calculate total CO2 from custom values
    total_custom_co2 = 0
    vehicle_count = 0
    total_speed = 0
    
    for lane in lanes.values():
        for car in lane:
            vehicle_count += 1
            if car.get("co2"):
                try:
                    total_custom_co2 += float(car["co2"])
                except (ValueError, TypeError):
                    pass
            total_speed += car.get("speed", 0)
    
    # Calculate average wait time
    stopped_vehicles = sum(
        1 for lane in lanes.values() 
        for car in lane 
        if car.get("speed", 0) < 0.5
    )
    
    avg_wait_time = (stopped_vehicles / max(vehicle_count, 1)) * 2.5
    
    # Map lanes to directions
    lane_mapping = {
        "north": ["-4_3", "-4_4"],
        "south": ["-69_4", "-69_3"],
        "east": ["-24_3", "24_4"],
        "west": ["-23_4", "23_3"]
    }
    
    cars = {
        "north": sum(len(lanes.get(lane, [])) for lane in lane_mapping["north"]),
        "south": sum(len(lanes.get(lane, [])) for lane in lane_mapping["south"]),
        "east": sum(len(lanes.get(lane, [])) for lane in lane_mapping["east"]),
        "west": sum(len(lanes.get(lane, [])) for lane in lane_mapping["west"])
    }
    
    return {
        "step": sim_data.get("step", 0),
        "co2": round(total_custom_co2 / 1000, 2),  # Convert to kg
        "avg_wait_time": round(avg_wait_time, 2),
        "cars": cars,
        "lights": sim_data.get("lights", latest_data["lights"]),
        "total_vehicles": vehicle_count
    }


def watch_emission_files():
    """Monitor emissionData directory for new files"""
    last_step = -1
    # Path to emission Data may defer for Windows
    EMISSION_DATA_PATH = "/SUMO/REV-4/emissionData" 

    print(f"Watching for emission files in: {os.path.abspath(EMISSION_DATA_PATH)}")
    
    while True:
        try:
            if not os.path.exists(EMISSION_DATA_PATH):
                print(f"Waiting for {EMISSION_DATA_PATH} to be created...")
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
                print(f"✓ Broadcasted step {step_num} to dashboard")
        
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
    print('✓ Client connected')
    with data_lock:
        emit('simulation_update', latest_data)


@socketio.on('disconnect')
def handle_disconnect():
    print('✗ Client disconnected')


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
