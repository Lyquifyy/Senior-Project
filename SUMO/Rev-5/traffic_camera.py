"""
Traffic light camera setup for CARLA.

This module creates a camera sensor at a traffic light location to monitor
the intersection for the traffic control system.
"""

import carla
import os
import weakref


class TrafficLightCamera:
    """
    Camera sensor attached to a traffic light location.
    Captures images of the intersection for monitoring and analysis.
    """
    
    def __init__(self, world, tls_id="238", output_dir="camera_output", save_interval=20):
        """
        Initialize camera at traffic light location.
        
        Args:
            world: CARLA world object
            tls_id: Traffic light ID (SUMO ID)
            output_dir: Directory to save camera images
            save_interval: Save every Nth frame (default: 10, i.e., save 1 out of 10 frames)
        """
        self.world = world
        self.tls_id = tls_id
        self.output_dir = output_dir
        self.save_interval = save_interval
        self.camera = None
        self.target_light = None
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Setup camera
        self._setup_camera()
    
    def _find_traffic_light(self):
        """
        Find the target traffic light in CARLA.
        Returns the first traffic light as fallback if specific ID not found.
        """
        traffic_lights = self.world.get_actors().filter('traffic.traffic_light*')
        
        print(f"[Camera] Found {len(traffic_lights)} traffic lights in CARLA")
        
        if len(traffic_lights) == 0:
            print("[Camera] ERROR: No traffic lights found!")
            return None
        
        # Convert tls_id to int for comparison (CARLA IDs are integers)
        try:
            target_id = int(self.tls_id)
        except ValueError:
            print(f"[Camera] Warning: Could not convert '{self.tls_id}' to integer")
            target_id = None
        
        # Try to find by ID
        if target_id is not None:
            for tl in traffic_lights:
                if tl.id == target_id:
                    print(f"[Camera] Found exact match for traffic light {self.tls_id}")
                    return tl
        
        # Fallback: use first traffic light and print all available
        print(f"[Camera] Traffic light {self.tls_id} not found by ID")
        
        # Get first 5 IDs without slicing (CARLA ActorList doesn't support slicing)
        sample_ids = []
        for i, tl in enumerate(traffic_lights):
            if i >= 5:
                break
            sample_ids.append(tl.id)
        
        print(f"[Camera] Available traffic light IDs (first 5): {sample_ids}")
        
        target = traffic_lights[0]
        print(f"[Camera] Using traffic light ID {target.id} at location {target.get_location()}")
        
        return target
    
    def _setup_camera(self):
        """Create and attach the camera sensor."""
        # Find traffic light
        self.target_light = self._find_traffic_light()
        
        if not self.target_light:
            print("[Camera] Cannot setup camera without traffic light")
            return
        
        # Get traffic light location
        tl_transform = self.target_light.get_transform()
        tl_location = tl_transform.location
        
        print(f"[Camera] Traffic light location: x={tl_location.x:.2f}, y={tl_location.y:.2f}, z={tl_location.z:.2f}")
        
        # Get camera blueprint
        blueprint_library = self.world.get_blueprint_library()
        camera_bp = blueprint_library.find('sensor.camera.rgb')
        
        # Configure camera
        camera_bp.set_attribute('image_size_x', '640')
        camera_bp.set_attribute('image_size_y', '480')
        camera_bp.set_attribute('fov', '90')  # Field of view
        

        # base_height = 20
        # base_pitch = -40

        # Optional: Rotate each camera to look toward intersection center
        yaw_adjustments = {
            70: 270,   # Camera 70 looks south
            71: 90,   # Camera 71 looks west
            72: 180,     # Camera 72 looks north
            73: 0     # Camera 73 looks east
        }

        # Get yaw for this camera, default to 0
        camera_yaw = yaw_adjustments.get(int(self.tls_id), 0)


        # Position offsets for each camera
        position_offsets = {
            70: (10, 0),    # Move east
            71: (-10, 0),    # Move north
            72: (0, -10),   # Move west
            73: (0, 10)    # Move south
        }

        offset_x, offset_y = position_offsets.get(int(self.tls_id), (10, 0))

        # Position camera above intersection, looking down
        camera_transform = carla.Transform(
            carla.Location(
                x=tl_location.x + offset_x,
                y=tl_location.y + offset_y,
                z=tl_location.z + 8  # 8 meters above traffic light
            ),
            carla.Rotation(
                pitch=-30,  # Looking down at 30 degrees
                yaw= camera_yaw,
                roll=0
            )
        )
        
        # Spawn camera
        self.camera = self.world.spawn_actor(camera_bp, camera_transform)
        
        # Use weak reference to avoid circular reference
        weak_self = weakref.ref(self)
        
        # Attach callback to process images
        self.camera.listen(lambda image: TrafficLightCamera._on_image(weak_self, image))
        
        print(f"[Camera] Camera spawned at x={camera_transform.location.x:.2f}, "
              f"y={camera_transform.location.y:.2f}, z={camera_transform.location.z:.2f}")
        print(f"[Camera] Images will be saved to: {self.output_dir}/")
    
    @staticmethod
    def _on_image(weak_self, image):
        """
        Callback when camera captures an image.
        
        Args:
            weak_self: Weak reference to TrafficLightCamera instance
            image: CARLA image object
        """
        self = weak_self()
        if not self:
            return
        
        # Only save every Nth frame to reduce I/O load
        if image.frame % self.save_interval != 0:
            return
        
        try:
            # Save image to disk
            filename = os.path.join(self.output_dir, f'frame_{image.frame:06d}.jpg')
            image.save_to_disk(filename)
            
            # Print confirmation
            print(f"[Camera] Saved frame {image.frame}")
        except Exception as e:
            print(f"[Camera] Error saving frame {image.frame}: {e}")
    
    def destroy(self):
        """Clean up camera sensor."""
        if self.camera is not None:
            self.camera.stop()
            self.camera.destroy()
            print("[Camera] Camera destroyed")
    
    def get_location(self):
        """Get the camera's current location."""
        if self.camera:
            return self.camera.get_location()
        return None
    
    def get_transform(self):
        """Get the camera's current transform."""
        if self.camera:
            return self.camera.get_transform()
        return None


def setup_camera(world, tls_id="238", output_dir="camera_output"):
    """
    Convenience function to setup a traffic light camera.
    
    Args:
        world: CARLA world object
        tls_id: Traffic light ID
        output_dir: Directory to save images
        
    Returns:
        TrafficLightCamera instance
    """
    return TrafficLightCamera(world, tls_id=tls_id, output_dir=output_dir)


# ==================================================================================================
# -- Test/Demo Script ------------------------------------------------------------------------------
# ==================================================================================================

if __name__ == '__main__':
    """
    Demo script to test the camera.
    Run this standalone to verify camera works.
    """
    import time
    
    print("="*60)
    print("Traffic Light Camera Test")
    print("="*60)
    
    # Connect to CARLA
    client = carla.Client('localhost', 2000)
    client.set_timeout(10.0)
    world = client.get_world()
    
    print(f"Connected to CARLA. World: {world.get_map().name}")
    
    # Setup camera
    camera = setup_camera(world, tls_id="238", output_dir="test_camera_output")
    
    if camera.camera:
        print("\nCamera is running! Check 'test_camera_output/' folder for images.")
        print("Let it run for 10 seconds...")
        
        try:
            time.sleep(10)
            print("\nTest complete!")
        except KeyboardInterrupt:
            print("\nStopped by user")
        finally:
            camera.destroy()
    else:
        print("\nFailed to setup camera")
    
    print("="*60)