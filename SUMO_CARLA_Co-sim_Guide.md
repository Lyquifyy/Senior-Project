# CARLA + SUMO Co-Simulation
## Setup and Run Guide

---

## 1. What You Need to Install

Before running anything, install the following three pieces of software. Order does not matter for installation, but CARLA must be launched before running the co-simulation script.

### CARLA Simulator

- Download CARLA 0.9.13 or later from: https://github.com/carla-simulator/carla/releases
- Extract the archive to a folder of your choice (e.g. `C:\CARLA` or `~/carla`).
- Install the Python client library in your Python environment:
  ```
  pip install carla
  ```
- Refer to https://carla.readthedocs.io/en/latest/adv_sumo/ for more detailed software information.

### SUMO

- Download SUMO 1.15.0 or later from: https://sumo.dlr.de/docs/Downloads.php
- Run the installer (Windows) or extract the package (Linux/macOS).
- After installing, set the `SUMO_HOME` environment variable to your SUMO installation folder. This is required — the co-simulation script will refuse to start without it.
- Refer to https://sumo.sourceforge.net/docs/ for more detailed software information.

**On Linux/macOS** (add to your `~/.bashrc` or `~/.zshrc` to make it permanent):
```bash
export SUMO_HOME=/path/to/sumo
export PATH=$PATH:$SUMO_HOME/bin
```

**On Windows (Command Prompt):**
```
set SUMO_HOME=C:\Program Files\Eclipse\Sumo
```

**On Windows (PowerShell):**
```
$env:SUMO_HOME = "C:\Program Files\Eclipse\Sumo"
```

Verify the variable is set correctly:
```
echo $SUMO_HOME
```

> **Note:** If `SUMO_HOME` is not set, you will see the error: `"please declare environment variable 'SUMO_HOME'"` and the script will exit immediately.

### Python Packages

With SUMO installed and `SUMO_HOME` set, make sure the following are available in your Python environment:

```
pip install carla
```

The `traci` module (used to communicate with SUMO) is bundled with SUMO. Add it to your Python path:

```bash
export PYTHONPATH=$PYTHONPATH:$SUMO_HOME/tools   # Linux/macOS
set PYTHONPATH=%PYTHONPATH%;%SUMO_HOME%\tools    # Windows CMD
```

---

## 2. Setting Up the Project Files

The custom files from our GitHub repo are placed directly in the SUMO subfolder:

```
C:\CARLA_0.9.16_Binary\Co-Simulation\Sumo
```

Whether using our additional files and subfolders or just what CARLA provides, it is important to place all files that communicate with `run_synchronization.py` directly in that same subfolder.

---

## 3. Running the Co-Simulation

### Step 1 — Start the CARLA Server

Always launch CARLA first and wait for it to finish loading before running the Python script.

**On Linux:**
```
./CarlaUE4.sh
```

**On Windows:**
```
CarlaUE4.exe
```

Wait until the Unreal Engine window is fully open and the map has loaded. CARLA will be listening on port 2000 by default.

### Step 2 — Run the Co-Simulation Script

Open a terminal in your project folder and run the following. This is the full command with all features enabled:

```bash
python run_synchronization.py Town03.sumocfg \
    --tls-manager sumo \
    --step-length 0.05 \
    --enable-traffic-control \
    --tls-id 238 \
    --enable-camera \
    --camera-tls-ids "70,71,72,73" \
    --camera-output-dir camera_output \
    --sync-vehicle-color \
    --sync-vehicle-lights \
    --sumo-gui
```

> **Note:** On Windows, replace the backslash `\` line continuation with `^` in CMD, or use a backtick `` ` `` in PowerShell.

**What each flag does:**

| Flag | What it does |
|------|-------------|
| `Town03.sumocfg` | The SUMO configuration file to load (required, always first). |
| `--tls-manager sumo` | Lets SUMO control the traffic light states. |
| `--step-length 0.05` | Both simulators advance 0.05 seconds per tick. |
| `--enable-traffic-control` | Activates traffic light phase cycling and emission logging. Also triggers automatic trip generation before the simulation starts. |
| `--tls-id 238` | The SUMO traffic light ID that the controller will manage. |
| `--enable-camera` | Spawns RGB camera sensors in CARLA above the specified traffic lights. |
| `--camera-tls-ids "70,71,72,73"` | The CARLA actor IDs of the four traffic lights to place cameras at. |
| `--camera-output-dir camera_output` | Folder where camera images are saved. |
| `--sync-vehicle-color` | Mirrors vehicle colors from SUMO into CARLA. |
| `--sync-vehicle-lights` | Mirrors vehicle indicator/light states between simulators. |
| `--sumo-gui` | Opens the SUMO graphical interface so you can watch the simulation. |

### Step 3 — Stop the Simulation

Press `Ctrl+C` in the terminal where `run_synchronization.py` is running. The script will clean up the cameras, restore CARLA to async mode, and close both simulator connections automatically.

---

## 4. What Happens When You Run It

For reference, here is the sequence of events after launching the script:

1. **Trip files are generated first.** Because `--enable-traffic-control` is set, the script reads `cars.csv`, creates vehicle types, generates random trips for the Town03 map, and runs the SUMO router to produce `custom.rou.xml` and `vtypes.xml`.
2. **SUMO launches via TraCI** using `Town03.sumocfg`. The SUMO GUI opens if `--sumo-gui` was passed.
3. **CARLA is contacted** on `127.0.0.1:2000`. Both simulators are locked into synchronous mode, so every step advances together.
4. **Four cameras are spawned** in CARLA above traffic lights 70, 71, 72, and 73, each looking toward the intersection center. Frames are saved to `camera_output/` every 20 steps.
5. **The sync loop runs.** Each step: SUMO advances, vehicles are mirrored into CARLA, CARLA advances, and the traffic controller cycles light phases and logs emissions.
6. **On `Ctrl+C`**, cameras are destroyed, CARLA resets, and both connections close.
