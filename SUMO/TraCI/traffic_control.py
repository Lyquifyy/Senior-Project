import traci

def run():
    traci.start(["sumo-gui", "-c", "map.sumocfg"])
    step = 0
    tls_id = "122216484"

    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()

        # Every 20 steps, switch to the next phase
        if step % 20 == 0:
            current_phase = traci.trafficlight.getPhase(tls_id)

            # Get the traffic light program definition
            programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)

            # Each program has a list of phases
            num_phases = len(programs[0].phases)

            next_phase = (current_phase + 1) % num_phases
            traci.trafficlight.setPhase(tls_id, next_phase)

        step += 1

    traci.close()

if __name__ == "__main__":
    run()
