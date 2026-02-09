# SUMO Traffic Simulation

Traffic simulation using [SUMO](https://eclipse.dev/sumo/) (Simulation of Urban MObility) with traffic light control, emission data collection, and optional CARLA co-simulation.

## Structure

| Folder | Description |
|--------|-------------|
| `Rev-1/` | Initial map, routes, trips |
| `Rev-2/` | Map with traffic lights (`tls.add.xml`) |
| `Rev-3-SimpleNetwork/` | Simplified network for testing |
| `Rev-4/` | Town03 map, traffic control, emission collection |
| `Rev-5/` | Advanced: CARLA integration, traffic plugins, synchronization |
| `CARLA/` | CARLA co-simulation (Rev-4_CARLA, Town03) |
| `TraCI-Testing/` | TraCI API testing and emission data |

## Running Rev-4

1. Run the trip generator to create routes:
   ```bash
   python trip_generator.py
   ```

2. Run traffic control:
   ```bash
   python traffic_control.py
   ```

### What `traffic_control.py` does

1. Loads and runs the `.sumocfg` file
2. Controls a specific traffic light via `change_light_phase`
3. Collects lane emissions via `collect_lane_emissions`
4. Uses `trip_generator.py` for custom route generation

## Rev-5

Includes CARLA co-simulation, `sumo_integration/`, traffic plugins, and `run_synchronization.py`. See `Rev-5/` and `CARLA/` for details.

## Emission Outputs

Emission data is written to `emissionData/` as JSON files (e.g., `lane_emissions_step_*.json`). These can be consumed by the Flask dashboard for visualization.