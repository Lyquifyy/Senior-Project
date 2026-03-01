# SUMO Traffic Simulation

Traffic simulation using [SUMO](https://eclipse.dev/sumo/) (Simulation of Urban MObility) with traffic light control, emission data collection, and optional CARLA co-simulation.

## Central runner (recommended)

All scenarios are run from one entry point. Set `SUMO_HOME` to your SUMO install, then from the project root:

**Standalone SUMO:**
```bash
python SUMO/run_simulation.py --scenario Rev-4 --mode standalone
python SUMO/run_simulation.py --scenario Rev-5 --mode standalone --sumo-gui
python SUMO/run_simulation.py --scenario TraCI-Testing --mode standalone
python SUMO/run_simulation.py --scenario Rev-3-SimpleNetwork --mode standalone
```

**CARLA co-simulation:**
```bash
python SUMO/run_simulation.py --scenario Rev-5 --mode carla --sumo-gui --enable-traffic-control
python SUMO/run_simulation.py --scenario Rev-4_CARLA --mode carla --sumo-gui
```

Optional: `--sim-end`, `--heavy-co2`, `--threshold`, `--tls-id` for standalone; `--carla-host`, `--carla-port`, `--enable-camera`, etc. for carla.

## Structure

| Folder | Description |
|--------|-------------|
| `core/` | Shared traffic control, trip generator, sumo_integration (single copy) |
| `Rev-1/` | Initial map, routes, trips |
| `Rev-2/` | Map with traffic lights (`tls.add.xml`) |
| `Rev-3-SimpleNetwork/` | Simplified network for testing |
| `Rev-4/` | Town03 map, traffic control, emission collection |
| `Rev-5/` | CARLA integration, traffic plugins, synchronization |
| `CARLA/Rev-4_CARLA/` | CARLA co-simulation scenario (Town03) |
| `TraCI-Testing/` | TraCI API testing and emission data |
| `scenarios.json` | Maps scenario names to paths; each scenario has `scenario.json` (sumocfg, tls_id, emission_dir, etc.) |

Per-revision `traffic_control.py` and `run_synchronization.py` are thin wrappers that invoke the central runner.

## Emission outputs and dashboard

Emission data is written to each scenario’s `emissionData/` (e.g. `lane_emissions_step_*.json`). The Flask dashboard reads from one emission directory: set `SUMO_EMISSION_DATA_PATH` to the path to use (e.g. `SUMO/Rev-5/emissionData` or `SUMO/Rev-4/emissionData`), or leave unset to default to `SUMO/Rev-5/emissionData`.