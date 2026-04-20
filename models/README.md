# Vehicle Classification

Python Script for vehicle type classification, used in the traffic simulation pipeline.

## Contents

| File | Description |
|------|-------------|
| `SVM_Training.ipynb` | Python script for training vehicle classification model |
| `SVM_Predict.py` | Python script for predicting vehicle type from images |


## Requirements

- Python
- EPA cleaned vehicle data (see `EPA Data Cleaning/`)

## Usage

1. Open `SVM_Training.ipynb` in VSCode or software of choice and make sure paths are set correctly
2. Run all code to train and save the model
3. Open `SVM_Predict.py` and choose an image to predict and run code

## Relation to Project

This model ties into:

- **EPA Data Cleaning** — Uses cleaned vehicle data for training/classification
- **SUMO** — Classification results inform vehicle type definitions and emission modeling
