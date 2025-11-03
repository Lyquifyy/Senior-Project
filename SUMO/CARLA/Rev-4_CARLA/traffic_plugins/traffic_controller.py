# Team traffic control logic for CARLA-SUMO co-simulation.

# This module contains the traffic light control and emission monitoring logic.


import traci
import json
import trip_generator
import time


# Traffic controller for managing traffic lights and collecting emission data.
class TrafficController:
    
    def __init__(self, tls_id="238"):
        # Initialize the traffic controller.
        
        self.tls_id = tls_id # Traffic light ID to control (default: "238")

        self.step_counter = 0
        
        # Hardcoded intervals (from original traffic_control.py)
        self.phase_interval = 200  # Change phase every 400 steps - 1 step = 0.5s, so every 400 steps = 20s
        self.emission_interval = 400  # Collect emissions every 400 steps
        
    def step(self, simulation_step):

        # Called every simulation step by run_synchronization.py.
        
        # simulation_step - Current simulation step number
        
        self.step_counter = simulation_step
        
        # Every 200 steps, switch to the next phase
        if simulation_step % self.phase_interval == 0 and simulation_step > 0:
            self.change_light_phase(self.tls_id)

        # Every 400 steps, collect emissions data
        if simulation_step % self.emission_interval == 0 and simulation_step > 0:
            self.collect_lane_emissions(self.tls_id, simulation_step)
    
    def change_light_phase(self, tls_id):
        current_phase = traci.trafficlight.getPhase(tls_id)

        # Get the traffic light program definition
        programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)

        # Each program has a list of phases
        num_phases = len(programs[0].phases)

        next_phase = (current_phase + 1) % num_phases
        traci.trafficlight.setPhase(tls_id, next_phase)


    def collect_lane_emissions(self, tls_id, step):
        lanes = traci.trafficlight.getControlledLanes(tls_id)
        lane_emissions = {}

        for lane_id in lanes:
            veh_ids = traci.lane.getLastStepVehicleIDs(lane_id)
            lane_emissions[lane_id] = []

            for vid in veh_ids:
                vtype_id = traci.vehicle.getTypeID(vid)
                custom_co2 = traci.vehicletype.getParameter(vtype_id, "customCO2")

                data = {
                    "vehicle_id": vid,
                    "co2": float(custom_co2) if custom_co2 else None,
                    "nox": traci.vehicle.getNOxEmission(vid),
                    "fuel": traci.vehicle.getFuelConsumption(vid),
                    "speed": traci.vehicle.getSpeed(vid)
                }
                lane_emissions[lane_id].append(data)

        # Wrap in a step-level JSON structure
        emissions_snapshot = {
            "step": step,
            "intersection": tls_id,
            "lanes": lane_emissions
        }

    # Serialize to JSON
        with open(f"emissionData/lane_emissions_step_{step}.json", "w") as f:        
            json.dump(emissions_snapshot, f, indent=2)