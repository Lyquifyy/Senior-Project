import trip_generator
import traci
import json
import time
# Procedure to change traffic light phase.
def change_light_phase(tls_id):
    current_phase = traci.trafficlight.getPhase(tls_id)

    # Get the traffic light program definition
    programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)

    # Each program has a list of phases
    num_phases = len(programs[0].phases)

    next_phase = (current_phase + 1) % num_phases
    traci.trafficlight.setPhase(tls_id, next_phase)

# Procedure to write emission data at traffic light.
def collect_lane_emissions(tls_id, step):
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


# Base SUMO procedure.
def run():
    traci.start(["sumo-gui", "-c", "Town03.sumocfg"])
    step = 0
    tls_id = "238"

    while traci.simulation.getMinExpectedNumber() > 0:
        time.sleep(0.5)
        traci.simulationStep()

        # Every 20 steps, switch to the next phase
        if step % 20 == 0:
            change_light_phase(tls_id)

        # Every 50 steps, collect emissions data and prepare for dashboard.
        if step % 50 == 0:
            collect_lane_emissions(tls_id, step)

        step += 1

    traci.close()

if __name__ == "__main__":

    # Run trip generator beforehand
    rou_file, vtypes_file = trip_generator.generate_trips(
        csv_file="cars.csv",
        net_file="Town03.net.xml",
        sim_end=500
    )

    # Run the actual simulation
    run()