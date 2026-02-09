# Dev Container – SUMO / CARLA

This dev container gives you a **local environment** to run:

- **SUMO** traffic simulation (Rev-4, Rev-5, TraCI) with traffic control and emissions
- **Flask** dashboard (port 8000)
- **CARLA** Python client for SUMO–CARLA co-simulation (when a CARLA server is available)

## What’s included

- **SUMO** from the [official Eclipse SUMO image](https://ghcr.io/eclipse-sumo/sumo)
- **Python 3** with `traci` and `sumolib` (via `SUMO_HOME/tools`)
- **CARLA** Python package (client only)
- **Flask** for the web dashboard

## Using the dev container

1. Open the project in VS Code / Cursor.
2. When prompted, **Reopen in Container** (or run **Dev Containers: Reopen in Container**).
3. After the image builds, you’re in the container with SUMO and Python ready.

### SUMO only (Rev-4)

From the container terminal:

```bash
cd SUMO/Rev-4
python3 trip_generator.py
python3 traffic_control.py
```

Use `sumo` (headless) or `sumo-gui` (if you have X11 forwarding) in your scripts as needed.

### Flask dashboard

```bash
cd flask
python3 test.py
```

Then open **http://localhost:8000** in your browser.

### SUMO + CARLA co-simulation (Rev-5)

The container has the **CARLA Python client** only. The **CARLA server** must run separately (it’s a separate process, often with a GPU).

**Option A – CARLA on your host**

1. Install and run [CARLA](https://carla.readthedocs.io/) on your host (e.g. port 2000).
2. From inside the dev container, connect to the host using **host.docker.internal** (or your host’s IP) and port **2000** when starting the co-simulation (e.g. in `run_synchronization.py` or your script’s CARLA host/port args).

**Option B – CARLA in another container**

Run CARLA in a second container (e.g. [carla Docker](https://carla.readthedocs.io/en/latest/adv_docker/)) and connect from this dev container to that container’s host/port (e.g. via a shared network).

Port **2000** is forwarded so you can reach a CARLA server on the host or another container.

## Ports

| Port | Use            |
|------|----------------|
| 8000 | Flask dashboard|
| 2000 | CARLA server   |

## Environment

- `SUMO_HOME` = `/usr/share/sumo`
- `PYTHONPATH` includes `$SUMO_HOME/tools` so `import traci` and `import sumolib` work.
