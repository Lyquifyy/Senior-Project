"""
Traffic light camera setup for CARLA.

This module creates a camera sensor at a traffic light location to monitor
intersections and optionally forward frames to a model via a callback.
"""

import carla
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
                    array = array[:, :, :3]
                    self.frame_callback(self.tls_id, array)
                except Exception as e:
                    print(f"[Camera {self.tls_id}] Error in frame_callback: {e}")

            self.save_queue.task_done()

    def find_traffic_light(self):
        traffic_lights = self.world.get_actors().filter('traffic.traffic_light*')
        print(f"[Camera] Found {len(traffic_lights)} traffic lights in CARLA")

        if len(traffic_lights) == 0:
            print("[Camera] ERROR: No traffic lights found!")
            return None

        try:
            target_id = int(self.tls_id)
        except ValueError:
            print(f"[Camera] Warning: Could not convert '{self.tls_id}' to integer")
            target_id = None

        if target_id is not None:
            for tl in traffic_lights:
                if tl.id == target_id:
                    print(f"[Camera] Found exact match for traffic light {self.tls_id}")
                    return tl

        sample_ids = []
        for i, tl in enumerate(traffic_lights):
            if i >= 5:
                break
            sample_ids.append(tl.id)

        print(f"[Camera] Traffic light {self.tls_id} not found by ID")
        print(f"[Camera] Available traffic light IDs (first 5): {sample_ids}")

        target = traffic_lights[0]
        print(f"[Camera] Using traffic light ID {target.id} at location {target.get_location()}")
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
        camera_bp.set_attribute('image_size_x', '1920')
        camera_bp.set_attribute('image_size_y', '1080')
        camera_bp.set_attribute('fov', '90')

        yaw_adjustments = {
            70: 270,
            71: 90,
            72: 180,
            73: 0,
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
            carla.Rotation(pitch=-30, yaw=camera_yaw, roll=0),
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
