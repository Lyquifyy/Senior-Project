# EPA Data Cleaning

EPA vehicle test data used for vehicle classifications and emission modeling in the traffic simulation pipeline.

## Contents

| Folder | Description |
|--------|-------------|
| `Raw Data/` | Original EPA test car datasets |
| `Clean Data/` | Processed outputs from cleaning scripts |
| `Scripts/` | Jupyter notebooks and cleaning pipeline |

## Raw Data

- **CSV** (`Raw Data/CSV/`) — Years 2008–2016 (`08tstcar.csv` through `16tstcar.csv`)
- **Excel** (`Raw Data/Excel/`) — Years 2017–2025 (e.g., `17tstcar-2018-05-30.xlsx`, `25-testcar-2025-05.xlsx`, `ExtraEPAData_diesel.xlsx`)

## Clean Data Outputs

| File | Description |
|------|-------------|
| `09to16_models.csv` | Model-level data for 2009–2016 |
| `17to25_models.csv` | Model-level data for 2017–2025 |
| `09to16_type_only.csv` | Vehicle type only (2009–2016) |
| `17to25_type_only.csv` | Vehicle type only (2017–2025) |
| `type_only_allyears.csv` | Vehicle type aggregated across all years |
| `clean_gasoline.csv` | Vehicle type aggregated across all years for gasoline vehicles|
| `clean_gasoline.csv` | Vehicle type aggregated across all years for diesel vehicles|

## Running the Cleaning Pipeline

1. Ensure dependencies are installed (pandas, openpyxl for Excel support).
2. Open `Scripts/Cleaning.ipynb` in Jupyter.
3. Run all cells; outputs are written to `Clean Data/`.

## Usage

Cleaned data is used by:

- **models/** — Vehicle classification model
- **SUMO/** — Vehicle type definitions and emission modeling
