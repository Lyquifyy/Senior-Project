#!/usr/bin/env python

# Copyright (c) 2020 Computer Vision Center (CVC) at the Universitat Autonoma de
# Barcelona (UAB).
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.
"""
Script to integrate CARLA and SUMO simulations
"""

# ==================================================================================================
# -- imports ---------------------------------------------------------------------------------------
# ==================================================================================================

import argparse
import logging
import os
import sys
import time
import trip_generator

# ==================================================================================================
# -- find traci module -----------------------------------------------------------------------------
# ==================================================================================================

if 'SUMO_HOME' in os.environ:
    sys.path.append(os.path.join(os.environ['SUMO_HOME'], 'tools'))
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")

# ==================================================================================================
# -- sumo integration imports ----------------------------------------------------------------------
# ==================================================================================================

from sumo_integration.bridge_helper import BridgeHelper  # pylint: disable=wrong-import-position
from sumo_integration.carla_simulation import CarlaSimulation  # pylint: disable=wrong-import-position
from sumo_integration.constants import INVALID_ACTOR_ID  # pylint: disable=wrong-import-position
from sumo_integration.sumo_simulation import SumoSimulation  # pylint: disable=wrong-import-position

# ==================================================================================================
# -- traffic control plugin import -----------------------------------------------------------------
# ==================================================================================================

try:
    from traffic_plugins.controller import step as traffic_control_step
    TRAFFIC_CONTROL_AVAILABLE = True
except ImportError as e:
    TRAFFIC_CONTROL_AVAILABLE = False
    traffic_control_step = None
    logging.warning(f"Traffic control plugin not available: {e}")


# ==================================================================================================
# -- traffic camera import -------------------------------------------------------------------------
# ==================================================================================================
try:
    from traffic_camera import TrafficLightCamera
    CAMERA_AVAILABLE = True
except ImportError as e:
    CAMERA_AVAILABLE = False
    logging.warning(f"Traffic camera not available: {e}")

from frame_feeder import MultiCameraFeeder
feeder = MultiCameraFeeder() 



# ==================================================================================================
# -- synchronization_loop --------------------------------------------------------------------------
# ==================================================================================================


class SimulationSynchronization(object):
    """
    SimulationSynchronization class is responsible for the synchronization of sumo and carla
    simulations.
    """
    def __init__(self,
                 sumo_simulation,
                 carla_simulation,
                 tls_manager='none',
                 sync_vehicle_color=False,
                 sync_vehicle_lights=False):

        self.sumo = sumo_simulation
        self.carla = carla_simulation

        self.tls_manager = tls_manager
        self.sync_vehicle_color = sync_vehicle_color
        self.sync_vehicle_lights = sync_vehicle_lights

        if tls_manager == 'carla':
            self.sumo.switch_off_traffic_lights()
        elif tls_manager == 'sumo':
            self.carla.switch_off_traffic_lights()

        # Mapped actor ids.
        self.sumo2carla_ids = {}  # Contains only actors controlled by sumo.
        self.carla2sumo_ids = {}  # Contains only actors controlled by carla.

        BridgeHelper.blueprint_library = self.carla.world.get_blueprint_library()
        BridgeHelper.offset = self.sumo.get_net_offset()

        # Configuring carla simulation in sync mode.
        settings = self.carla.world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = self.carla.step_length
        self.carla.world.apply_settings(settings)

        traffic_manager = self.carla.client.get_trafficmanager()
        traffic_manager.set_synchronous_mode(True)

    def tick(self):
        """
        Tick to simulation synchronization
        """
        # -----------------
        # sumo-->carla sync
        # -----------------
        self.sumo.tick()

        # Spawning new sumo actors in carla (i.e, not controlled by carla).
        sumo_spawned_actors = self.sumo.spawned_actors - set(self.carla2sumo_ids.values())
        for sumo_actor_id in sumo_spawned_actors:
            self.sumo.subscribe(sumo_actor_id)
            sumo_actor = self.sumo.get_actor(sumo_actor_id)

            carla_blueprint = BridgeHelper.get_carla_blueprint(sumo_actor, self.sync_vehicle_color)
            if carla_blueprint is not None:
                carla_transform = BridgeHelper.get_carla_transform(sumo_actor.transform,
                                                                   sumo_actor.extent)

                carla_actor_id = self.carla.spawn_actor(carla_blueprint, carla_transform)
                if carla_actor_id != INVALID_ACTOR_ID:
                    self.sumo2carla_ids[sumo_actor_id] = carla_actor_id
            else:
                self.sumo.unsubscribe(sumo_actor_id)

        # Destroying sumo arrived actors in carla.
        for sumo_actor_id in self.sumo.destroyed_actors:
            if sumo_actor_id in self.sumo2carla_ids:
                self.carla.destroy_actor(self.sumo2carla_ids.pop(sumo_actor_id))

        # Updating sumo actors in carla.
        for sumo_actor_id in self.sumo2carla_ids:
            carla_actor_id = self.sumo2carla_ids[sumo_actor_id]

            sumo_actor = self.sumo.get_actor(sumo_actor_id)
            carla_actor = self.carla.get_actor(carla_actor_id)

            carla_transform = BridgeHelper.get_carla_transform(sumo_actor.transform,
                                                               sumo_actor.extent)
            if self.sync_vehicle_lights:
                carla_lights = BridgeHelper.get_carla_lights_state(carla_actor.get_light_state(),
                                                                   sumo_actor.signals)
            else:
                carla_lights = None

            self.carla.synchronize_vehicle(carla_actor_id, carla_transform, carla_lights)

        # Updates traffic lights in carla based on sumo information.
        if self.tls_manager == 'sumo':
            common_landmarks = self.sumo.traffic_light_ids & self.carla.traffic_light_ids
            for landmark_id in common_landmarks:
                sumo_tl_state = self.sumo.get_traffic_light_state(landmark_id)
                carla_tl_state = BridgeHelper.get_carla_traffic_light_state(sumo_tl_state)

                self.carla.synchronize_traffic_light(landmark_id, carla_tl_state)

        # -----------------
        # carla-->sumo sync
        # -----------------
        self.carla.tick()

        # Spawning new carla actors (not controlled by sumo)
        carla_spawned_actors = self.carla.spawned_actors - set(self.sumo2carla_ids.values())
        for carla_actor_id in carla_spawned_actors:
            carla_actor = self.carla.get_actor(carla_actor_id)

            type_id = BridgeHelper.get_sumo_vtype(carla_actor)
            color = carla_actor.attributes.get('color', None) if self.sync_vehicle_color else None
            if type_id is not None:
                sumo_actor_id = self.sumo.spawn_actor(type_id, color)
                if sumo_actor_id != INVALID_ACTOR_ID:
                    self.carla2sumo_ids[carla_actor_id] = sumo_actor_id
                    self.sumo.subscribe(sumo_actor_id)

        # Destroying required carla actors in sumo.
        for carla_actor_id in self.carla.destroyed_actors:
            if carla_actor_id in self.carla2sumo_ids:
                self.sumo.destroy_actor(self.carla2sumo_ids.pop(carla_actor_id))

        # Updating carla actors in sumo.
        for carla_actor_id in self.carla2sumo_ids:
            sumo_actor_id = self.carla2sumo_ids[carla_actor_id]

            carla_actor = self.carla.get_actor(carla_actor_id)
            sumo_actor = self.sumo.get_actor(sumo_actor_id)

            sumo_transform = BridgeHelper.get_sumo_transform(carla_actor.get_transform(),
                                                             carla_actor.bounding_box.extent)
            if self.sync_vehicle_lights:
                carla_lights = self.carla.get_actor_light_state(carla_actor_id)
                if carla_lights is not None:
                    sumo_lights = BridgeHelper.get_sumo_lights_state(sumo_actor.signals,
                                                                     carla_lights)
                else:
                    sumo_lights = None
            else:
                sumo_lights = None

            self.sumo.synchronize_vehicle(sumo_actor_id, sumo_transform, sumo_lights)

        # Updates traffic lights in sumo based on carla information.
        if self.tls_manager == 'carla':
            common_landmarks = self.sumo.traffic_light_ids & self.carla.traffic_light_ids
            for landmark_id in common_landmarks:
                carla_tl_state = self.carla.get_traffic_light_state(landmark_id)
                sumo_tl_state = BridgeHelper.get_sumo_traffic_light_state(carla_tl_state)

                # Updates all the sumo links related to this landmark.
                self.sumo.synchronize_traffic_light(landmark_id, sumo_tl_state)

    def close(self):
        """
        Cleans synchronization.
        """
        # Configuring carla simulation in async mode.
        settings = self.carla.world.get_settings()
        settings.synchronous_mode = False
        settings.fixed_delta_seconds = None
        self.carla.world.apply_settings(settings)

        # Destroying synchronized actors.
        for carla_actor_id in self.sumo2carla_ids.values():
            self.carla.destroy_actor(carla_actor_id)

        for sumo_actor_id in self.carla2sumo_ids.values():
            self.sumo.destroy_actor(sumo_actor_id)

        # Closing sumo and carla client.
        self.carla.close()
        self.sumo.close()


def synchronization_loop(args):
    """
    Entry point for sumo-carla co-simulation.
    """
    sumo_simulation = SumoSimulation(args.sumo_cfg_file, args.step_length, args.sumo_host,
                                     args.sumo_port, args.sumo_gui, args.client_order)
    carla_simulation = CarlaSimulation(args.carla_host, args.carla_port, args.step_length)

    synchronization = SimulationSynchronization(sumo_simulation, carla_simulation, args.tls_manager,
                                                args.sync_vehicle_color, args.sync_vehicle_lights)
    
    # ==================================================================================================
    # -- TRAFFIC CONTROL PLUGIN INITIALIZATION -----------------------------------------------------
    # ==================================================================================================
    
    # Check if traffic control is available and requested
    if not TRAFFIC_CONTROL_AVAILABLE and args.enable_traffic_control:
        logging.warning("Traffic control requested but traffic_plugins.controller not available")
        logging.warning("Make sure traffic_control.py is in the correct directory")
    elif TRAFFIC_CONTROL_AVAILABLE and args.enable_traffic_control:
        logging.info(f"Traffic control enabled for traffic light ID: {args.tls_id}")


    # ==================================================================================================
    # -- TRAFFIC CAMERA INITIALIZATION -----------------------------------------------------
    # ==================================================================================================
    traffic_cameras = []  # List to hold multiple cameras

    if CAMERA_AVAILABLE and args.enable_camera:
        # If camera_tls_ids is provided, use those; otherwise use single camera_tls_id
        if args.camera_tls_ids:
            camera_ids = [int(id.strip()) for id in args.camera_tls_ids.split(',')]
        elif args.camera_tls_id:
            camera_ids = [int(args.camera_tls_id)]
        else:
            camera_ids = [int(args.tls_id)]


        # Loop through each ID and create a camera
        for camera_id in camera_ids:
            if len(camera_ids) > 1:
                output_dir = os.path.join(args.camera_output_dir, f"camera_{camera_id}")
                # e.g., "camera_output/camera_70/"
            else:
                output_dir = args.camera_output_dir
                # e.g., "camera_output/"
            camera = TrafficLightCamera(
                carla_simulation.world,
                tls_id=str(camera_id),
                output_dir=output_dir,
                save_interval=20,
                frame_callback=feeder.on_frame
            )
            traffic_cameras.append(camera)  # Add to list
        logging.info(f"Traffic camera enabled, saving to: {args.camera_output_dir}/")

 
    # ==================================================================================================
    
    step = 0
    try:
        while True:
            start = time.time()

            synchronization.tick()

            # ==================================================================================================
            # -- TRAFFIC CONTROL PLUGIN UPDATE ---------------------------------------------------------
            # ==================================================================================================
            if TRAFFIC_CONTROL_AVAILABLE and args.enable_traffic_control:
                traffic_control_step(tls_id=args.tls_id, simulation_step=step)
            # ==================================================================================================
            
            step += 1

            end = time.time()
            elapsed = end - start
            if elapsed < args.step_length:
                time.sleep(args.step_length - elapsed)

    except KeyboardInterrupt:
        logging.info('Cancelled by user.')

    finally:
        logging.info('Cleaning synchronization')

        # Cleanup cameras
        for camera in traffic_cameras:
            try:
                camera.destroy()
            except:
                pass
        synchronization.close()


def generate_trips_if_needed(args):
    """
    Generate trips using trip_generator before starting co-simulation.
    This is called automatically if the traffic control plugin is enabled.
    """
    if not TRAFFIC_CONTROL_AVAILABLE or not args.enable_traffic_control:
        return
    
    else:
        # Import trip_generator
        import sys
        trip_gen_path = os.path.join(os.path.dirname(__file__), 'Test', 'Rev-4')
        if trip_gen_path not in sys.path:
            sys.path.insert(0, trip_gen_path)
                
        # Parameters from original traffic_control.py
        heavyCO2Percent = 0.3  # Change this value between 0 and 1 to adjust heavy vehicle ratio
        threshold = 250.0  # CO2 threshold in g/mi to classify heavy vs light vehicles
        
        # Run trip generator
        logging.info("Generating trips for simulation...")
        rou_file, vtypes_file = trip_generator.generate_trips(
            csv_file="cars.csv",
            net_file="Town03.net.xml",
            sim_end=300,
            heavyCO2Percent=heavyCO2Percent,
            threshold=threshold
        )
        logging.info(f"Trip generation complete. Routes: {rou_file}, VTypes: {vtypes_file}")


# if __name__ == '__main__':
#     # Thin wrapper: use central runner. Run: python SUMO/run_simulation.py --scenario Rev-5 --mode carla [options]
#     import subprocess
#     _argv = sys.argv[1:]
#     if _argv and (_argv[0].endswith('.sumocfg') or 'Town03' in _argv[0]):
#         _argv = _argv[1:]  # drop positional sumo_cfg_file
#     _run = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'run_simulation.py')
#     _run = os.path.normpath(_run)
#     sys.exit(subprocess.run([sys.executable, _run, '--scenario', 'Rev-5', '--mode', 'carla'] + _argv).returncode)

if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description=__doc__)
    argparser.add_argument('sumo_cfg_file', type=str, help='sumo configuration file')
    argparser.add_argument('--carla-host', default='127.0.0.1')
    argparser.add_argument('--carla-port', type=int, default=2000)
    argparser.add_argument('--sumo-host', default=None)
    argparser.add_argument('--sumo-port', type=int, default=None)
    argparser.add_argument('--sumo-gui', action='store_true')
    argparser.add_argument('--step-length', type=float, default=0.05)
    argparser.add_argument('--client-order', type=int, default=1)
    argparser.add_argument('--sync-vehicle-lights', action='store_true')
    argparser.add_argument('--sync-vehicle-color', action='store_true')
    argparser.add_argument('--sync-vehicle-all', action='store_true')
    argparser.add_argument('--tls-manager', choices=['none', 'sumo', 'carla'], default='none')
    argparser.add_argument('--enable-traffic-control', action='store_true')
    argparser.add_argument('--tls-id', type=str, default='238')
    argparser.add_argument('--enable-camera', action='store_true')
    argparser.add_argument('--camera-tls-id', type=str, default='70')
    argparser.add_argument('--camera-tls-ids', type=str, default=None)
    argparser.add_argument('--camera-output-dir', type=str, default='camera_output')
    argparser.add_argument('--debug', action='store_true')
    arguments = argparser.parse_args()

    if arguments.sync_vehicle_all:
        arguments.sync_vehicle_lights = True
        arguments.sync_vehicle_color = True

    if arguments.debug:
        logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.DEBUG)
    else:
        logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)

    synchronization_loop(arguments)