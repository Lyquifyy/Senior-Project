# Traffic Simulation Dashboard

Flask-based web dashboard for visualizing traffic simulation data: emissions, lane occupancy, traffic light states, and vehicle counts.

## Project Structure

| File/Folder | Description |
|-------------|-------------|
| `test.py` | Flask app (routes, `SIMULATION_DATA`, `process_data`) |
| `templates/test.html` | Main dashboard template |
| `static/index.css` | Styles |
| `static/index.js` | Frontend logic |

## Data Flow

The dashboard reads simulation data (currently from `SIMULATION_DATA` in `test.py`). In production, this can be wired to live SUMO emission output or the `emissionData/*.json` files from the SUMO simulation.

## Setup

### Dependencies

**Windows:**
```bash
pip install flask
```

**Linux (e.g., Kali):**
```bash
pipx install flask
```

### Run

```bash
python test.py
```

Then open `http://127.0.0.1:5000` in a browser.

## Routes

- `GET /` — Dashboard (HTML)
- `GET /get` — JSON data (CO₂, cars per direction, light states)