## To run Rev-4, you will need to run traffic_control.py

### `python trip_generator.py`

## A brief overview of what exactly traffic_control.py is doing:
1. Running the .sumocfg file given.
2. Controlling a specific traffic light using the change_light_phase function.
3. Collect traffic emissions data through collect_lane_emissions function.
3. Generating custom routes using trip_generator.py.