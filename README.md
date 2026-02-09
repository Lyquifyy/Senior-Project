# Senior Project

Traffic simulation and vehicle emissions analysis project integrating EPA vehicle data, SUMO traffic simulation, and a web-based dashboard for visualization.

## Project Overview

This repository contains:

- **EPA Data** — Raw and cleaned EPA vehicle test data (2008–2025) used for vehicle classifications and emission modeling
- **SUMO Simulation** — Traffic simulation using SUMO (Simulation of Urban MObility) with traffic light control and emission data collection
- **Web Dashboard** — Flask-based visualization of simulation data (emissions, traffic lights, lane occupancy)
- **Vehicle Classification** — MATLAB model for vehicle type classification (in `models/`)

## Directory Structure

```
├── EPA Data Cleaning/    # EPA vehicle test data and cleaning scripts
├── flask/                # Web dashboard for traffic/emission visualization
├── models/               # Vehicle classification (MATLAB)
├── SUMO/                 # Traffic simulation (Rev-1 through Rev-5, CARLA integration)
└── README.md
```

## Quick Start

1. **EPA Data** — See [EPA Data Cleaning/README.md](EPA%20Data%20Cleaning/README.md) for data pipeline and cleaning
2. **SUMO** — See [SUMO/README.md](SUMO/README.md) for running traffic simulations
3. **Dashboard** — See [flask/README.md](flask/README.md) for running the web app

## Prerequisites

- Python 3.x
- SUMO (Simulation of Urban MObility)
- MATLAB (for vehicle classification model)
- Flask (for web dashboard)
