"""
Traffic light camera setup for CARLA.

This module creates a camera sensor at a traffic light location to monitor
intersections and optionally forward frames to a model via a callback.
"""

import carla
import math
import os
import weakref
import threading
import queue

import numpy as np


class TrafficLightCamera:
    """
    Camera sensor attached to a traffic light location.
    Captures images of the intersection for monitoring and analysis.

    Also accepts a frame_callback to forward frames as numpy arrays directly to
    a model or downstream system.
    """

    def __init__(self, world, tls_id="238", output_dir="camera_output", save_interval=20, frame_callback=None):
        self.world = world
        self.tls_id = tls_id
        self.output_dir = output_dir
        self.save_interval = save_interval
        self.frame_callback = frame_callback
        self.camera = None
        self.target_light = None

        self.save_queue = queue.Queue(maxsize=10)
        self.save_thread = threading.Thread(target=self.save_worker, daemon=True)
        self.save_thread.start()

        os.makedirs(output_dir, exist_ok=True)
        self.setup_camera()

    def save_worker(self):
        while True:
            item = self.save_queue.get()
            if item is None:
                break

            filename, image = item
            try:
                image.save_to_disk(filename)
                print(f"[Camera {self.tls_id}] Saved {filename}")
            except Exception as e:
                print(f"[Camera {self.tls_id}] Error saving frame to {filename}: {e}")

            if self.frame_callback is not None:
                try:
                    array = np.frombuffer(image.raw_data, dtype=np.uint8)
                    array = array.reshape((image.height, image.width, 4))
                    array = array[:, :, :3][:, :, ::-1]  # BGRA[:,:,:3] is BGR; flip to RGB
                    self.frame_callback(self.tls_id, array)
                except Exception as e:
                    print(f"[Camera {self.tls_id}] Error in frame_callback: {e}")

            self.save_queue.task_done()

    def find_traffic_light(self):
        all_tls = list(self.world.get_actors().filter('traffic.traffic_light*'))
        print(f"[Camera] Found {len(all_tls)} traffic lights in CARLA")

        if not all_tls:
            print("[Camera] ERROR: No traffic lights found!")
            return None

        # Try exact CARLA actor ID match first (works when SUMO has assigned IDs)
        try:
            target_id = int(self.tls_id)
            for tl in all_tls:
                if tl.id == target_id:
                    print(f"[Camera] Found exact match for traffic light {self.tls_id}")
                    return tl
        except ValueError:
            pass

        # Fallback: find the main 4-way junction by grouping lights and scoring
        # by how many quadrants around the group centroid are covered.
        print(f"[Camera] ID {self.tls_id} not found — searching for main junction ...")
        visited = set()
        junctions = []
        for tl in all_tls:
            if tl.id in visited:
                continue
            try:
                group = list(tl.get_group_traffic_lights())
                for t in group:
                    visited.add(t.id)
            except Exception:
                group = [tl]
                visited.add(tl.id)
            junctions.append(group)

        def _score(group):
            if len(group) < 2:
                return 0
            locs = [t.get_location() for t in group]
            cx = sum(l.x for l in locs) / len(locs)
            cy = sum(l.y for l in locs) / len(locs)
            angles = [math.degrees(math.atan2(l.y - cy, l.x - cx)) % 360
                      for l in locs]
            quadrants = len(set(int(a / 90) for a in angles))
            return quadrants * 100 + len(group)

        best_group = max(junctions, key=_score)
        locs = [t.get_location() for t in best_group]
        cx = sum(l.x for l in locs) / len(locs)
        cy = sum(l.y for l in locs) / len(locs)

        # All cameras use the TL nearest the centroid as a shared anchor so
        # that the ±10 m position offsets in setup_camera() are applied from
        # the same centre point for every camera.  Using per-camera approach
        # directions here shifted each anchor to a different spot, breaking the
        # layout calibrated against a common centre.
        target = min(best_group,
                     key=lambda t: (t.get_location().x - cx) ** 2
                                   + (t.get_location().y - cy) ** 2)
        print(f"[Camera] Junction fallback: using TL id={target.id} "
              f"at ({target.get_location().x:.1f}, {target.get_location().y:.1f}) "
              f"[junction centre ({cx:.1f}, {cy:.1f})]")
        return target

    def setup_camera(self):
        self.target_light = self.find_traffic_light()
        if not self.target_light:
            print("[Camera] Cannot setup camera without traffic light")
            return

        tl_transform = self.target_light.get_transform()
        tl_location = tl_transform.location

        print(f"[Camera] Traffic light location: x={tl_location.x:.2f}, y={tl_location.y:.2f}, z={tl_location.z:.2f}")

        blueprint_library = self.world.get_blueprint_library()
        camera_bp = blueprint_library.find('sensor.camera.rgb')
        camera_bp.set_attribute('image_size_x', '256')
        camera_bp.set_attribute('image_size_y', '256')
        camera_bp.set_attribute('fov', '45')

        yaw_adjustments = {
            70: 260,
            71: 80,
            72: 170,
            73: 350,
        }
        camera_yaw = yaw_adjustments.get(int(self.tls_id), 0)
        position_offsets = {
            70: (10, 0),
            71: (-10, 0),
            72: (0, -10),
            73: (0, 10),
        }
        offset_x, offset_y = position_offsets.get(int(self.tls_id), (10, 0))

        camera_transform = carla.Transform(
            carla.Location(
                x=tl_location.x + offset_x,
                y=tl_location.y + offset_y,
                z=tl_location.z + 8,
            ),
            carla.Rotation(pitch=-10, yaw=camera_yaw, roll=0),
        )

        self.camera = self.world.spawn_actor(camera_bp, camera_transform)
        weak_self = weakref.ref(self)
        self.camera.listen(lambda image: TrafficLightCamera.on_image(weak_self, image))

        print(f"[Camera] Camera spawned at x={camera_transform.location.x:.2f}, y={camera_transform.location.y:.2f}, z={camera_transform.location.z:.2f}")
        print(f"[Camera] Images will be saved to: {self.output_dir}/")
        if self.frame_callback is not None:
            print(f"[Camera {self.tls_id}] frame_callback registered, frames will be sent to model.")

    @staticmethod
    def on_image(weak_self, image):
        self = weak_self()
        if not self:
            return
        if image.frame % self.save_interval != 0:
            return
        try:
            filename = os.path.join(self.output_dir, f'frame_{image.frame:06d}.png')
            self.save_queue.put_nowait((filename, image))
        except queue.Full:
            print(f"[Camera {self.tls_id}] Warning: Save queue full. Dropping frame {image.frame}")

    def destroy(self):
        if self.camera is not None:
            self.camera.stop()
            self.camera.destroy()
            print(f"[Camera {self.tls_id}] Camera destroyed")
        self.save_queue.put(None)
        self.save_thread.join(timeout=5)

    def get_location(self):
        if self.camera:
            return self.camera.get_location()
        return None

    def get_transform(self):
        if self.camera:
            return self.camera.get_transform()
        return None


def setup_camera(world, tls_id="238", output_dir="camera_output", frame_callback=None):
    return TrafficLightCamera(world, tls_id=tls_id, output_dir=output_dir, frame_callback=frame_callback)
