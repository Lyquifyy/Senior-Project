# Vehicle Classification

MATLAB Live Script for vehicle type classification, used in the traffic simulation pipeline.

## Contents

| File | Description |
|------|-------------|
| `Vehicle_Classification.mlx` | MATLAB Live Script for classification model |

## Requirements

- MATLAB (with required toolboxes for the script)
- EPA cleaned vehicle data (see `EPA Data Cleaning/`)

## Usage

1. Open `Vehicle_Classification.mlx` in MATLAB.
2. Ensure EPA cleaned data is available or paths are set correctly.
3. Run the Live Script sections as needed.

## Relation to Project

This model ties into:

- **EPA Data Cleaning** — Uses cleaned vehicle data for training/classification
- **SUMO** — Classification results inform vehicle type definitions and emission modeling
