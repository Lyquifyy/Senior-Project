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

# Network-wide emission-aware control (all 11 Town03 intersections):
python SUMO/run_simulation.py --scenario Carbon-Emission-Traffic --mode standalone
python SUMO/run_simulation.py --scenario Carbon-Emission-Traffic --mode standalone --sumo-gui
```

**CARLA co-simulation:**
```bash
python SUMO/run_simulation.py --scenario Rev-5 --mode carla --sumo-gui --enable-traffic-control
python SUMO/run_simulation.py --scenario Rev-4_CARLA --mode carla --sumo-gui

# Network-wide with SUMO as traffic-light authority:
python SUMO/run_simulation.py --scenario Carbon-Emission-Traffic --mode carla --sumo-gui --enable-traffic-control --tls-manager sumo
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
| `Carbon-Emission-Traffic/` | Network-wide emission-aware control of all 11 Town03 intersections |
| `CARLA/Rev-4_CARLA/` | CARLA co-simulation scenario (Town03) |
| `TraCI-Testing/` | TraCI API testing and emission data |
| `scenarios.json` | Maps scenario names to paths; each scenario has `scenario.json` (sumocfg, tls_id, emission_dir, etc.) |

Per-revision `traffic_control.py` and `run_synchronization.py` are thin wrappers that invoke the central runner.

## Emission outputs and dashboard

**Legacy scenarios (Rev-4, Rev-5, TraCI-Testing):**
Emission data is written to each scenario's missionData/lane_emissions_step_*.json (single intersection per file).

**Carbon-Emission-Traffic scenario (network-wide):**
Three logging layers are written on every run:
- missionData/network_step_*.json - full network snapshot per interval (all 11 intersections with per-lane CO2/NOx/fuel/speed and network totals)
- logs/phase_decisions.jsonl - JSONL record for every traffic-light phase switch attempt
- logs/run_summary.json - end-of-run totals (CO2 kg, fuel, vehicles completed)
- missionData/sumo_emissions.xml, logs/sumo_tripinfo.xml, logs/sumo_summary.xml - SUMO built-in outputs for auditing

**Dashboard:**
The Flask dashboard (Web_dashboard/test.py) auto-detects both formats. Set SUMO_EMISSION_DATA_PATH to point to the scenario emissionData directory.

    SUMO_EMISSION_DATA_PATH=SUMO/Carbon-Emission-Traffic/emissionData python Web_dashboard/test.py

**Validation:**
After a run, verify TLS coverage and compare adaptive vs fixed-time performance:

    python SUMO/Carbon-Emission-Traffic/validate_run.py

Copy logs/run_summary.json from a fixed-time run to logs/run_summary_baseline.json and rerun for a CO2/fuel/throughput delta report.