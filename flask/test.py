from flask import Flask, jsonify, render_template


app = Flask(__name__)


# --- Static simulation data (your JSON) ---
SIMULATION_DATA = {
   "step": 100,
   "intersection": "238",
   "lanes": {
       "-23_4": [],
       "-4_3": [
           {
               "vehicle_id": "83",
               "co2": "",
               "nox": 0.6117,
               "fuel": 470.9122621847468,
               "speed": 0.0
           }
       ],
       "24_4": [
           {
               "vehicle_id": "49",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           }
       ],
       "-4_4": [],
       "-69_3": [
           {
               "vehicle_id": "72",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           },
           {
               "vehicle_id": "74",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           },
           {
               "vehicle_id": "48",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           },
           {
               "vehicle_id": "76",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           },
           {
               "vehicle_id": "43",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           },
           {
               "vehicle_id": "35",
               "co2": "",
               "nox": 0.6117,
               "fuel": 493.1,
               "speed": 0.0
           }
       ],
       "-69_4": [
           {
               "vehicle_id": "84",
               "co2": "",
               "nox": 0.6111910087336376,
               "fuel": 458.486589899808,
               "speed": 0.07625269093068833
           }
       ]
   }
}


# --- Helper to convert raw data to dashboard format ---
def process_data(sim_data):
    lanes = sim_data["lanes"]

    total_fuel = sum(car["fuel"] for lane in lanes.values() for car in lane)

    north = len(lanes.get("-4_3", []))
    south = len(lanes.get("24_4", []))
    east = len(lanes.get("-69_3", []))
    west = len(lanes.get("-69_4", []))

    return {
        "co2": round(total_fuel / 100, 2),
        "avg_wait_time": 2.3,

        "cars": {
            "north": north,
            "south": south,
            "east": east,
            "west": west
        },

        "lights": {
            "north": "red",
            "south": "red",
            "east": "green",
            "west": "red"
        }
    }



@app.route('/')
def home():
   return render_template('test.html')


@app.route('/get', methods=['GET'])
def get_data():
   return jsonify(process_data(SIMULATION_DATA))




if __name__ == '__main__':
   app.run(debug=True)


