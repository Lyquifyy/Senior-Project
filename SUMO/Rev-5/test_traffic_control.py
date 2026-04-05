import unittest
import traci
import json
import warnings

warnings.simplefilter("ignore")

from traffic_control import (
    decide_next_phase,
    compute_lane_metrics,
    collect_lane_emissions,
)

SUMO_CMD = ["sumo-gui", "-c", "Town03.sumocfg", "--no-warnings", "--no-step-log"]
TLS_ID = "238"
TOTAL_STEPS = 250       


class TestTrafficControlLive(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        traci.start(SUMO_CMD)
        for _ in range(TOTAL_STEPS):
            traci.simulationStep()
        print(f"\n[Setup] SUMO started. Simulated for {TOTAL_STEPS} steps.")

    @classmethod
    def tearDownClass(cls):
        traci.close()
        print("\n[Teardown] SUMO closed.")

    def test_metrics_returns_all_controlled_lanes(self):
        """Every controlled lane should have an entry in the result."""
        controlled = list(traci.trafficlight.getControlledLanes(TLS_ID))
        metrics = compute_lane_metrics(TLS_ID)

        print(f"\n  Controlled lanes ({len(controlled)}): {controlled}")
        print(f"  Metrics returned for {len(metrics)} lanes:")
        for lane, data in metrics.items():
            print(f"    {lane} -> queue: {data['queue']}, co2: {data['co2']:.2f}")

        for lane in controlled:
            self.assertIn(lane, metrics)

    def test_decide_next_phase_returns_valid_phase_index(self):
        """Returned phase must be a valid index in the TLS program."""
        programs = traci.trafficlight.getCompleteRedYellowGreenDefinition(TLS_ID)
        num_phases = len(programs[0].phases)
        current_phase = traci.trafficlight.getPhase(TLS_ID)
        result = decide_next_phase(TLS_ID)

        print(f"\n  Total phases in program: {num_phases}")
        print(f"  Current phase: {current_phase}")
        print(f"  decide_next_phase returned: {result}")

        self.assertGreaterEqual(result, 0)
        self.assertLess(result, num_phases)

    def test_emission_file_has_correct_schema(self):
        """JSON output must contain step, intersection, and lanes keys."""
        collect_lane_emissions(TLS_ID, step=9999)
        from traffic_control import EMISSION_DIR
        out_file = EMISSION_DIR / "lane_emissions_step_9999.json"
        data = json.loads(out_file.read_text())

        print(f"\n  File written to: {out_file}")
        print(f"  Step: {data['step']}, Intersection: {data['intersection']}")
        print(f"  Lanes in output ({len(data['lanes'])}):")
        for lane, vehicles in data["lanes"].items():
            print(f"    {lane} -> {len(vehicles)} vehicle(s)")

        self.assertEqual(data["step"], 9999)
        self.assertEqual(data["intersection"], TLS_ID)
        self.assertIn("lanes", data)
        out_file.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)