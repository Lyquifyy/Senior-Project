"""
Camera output frame reader

Reads frames from the camera output directory in real time and feeds them into a queue for saving and use by the model.
"""

import os
import time
import threading
import queue
import logging
from pathlib import Path


class CameraReader:

    def __init__(self, camera_output_dir: str, frame_interval: float = 0.2):
        """
        Initialize CameraReader.

        Args:
            camera_output_dir: Directory where camera frames are saved.
            frame_interval: Time interval (in seconds) to check for new frames.
        """
        self.camera_output_dir = Path(camera_output_dir)
        self.frame_interval = frame_interval # how often to check for new frames (default: 0.2s = 5 FPS)
        self.seen = set() # to track seen frames and avoid duplicates
        self.frame_queue = queue.Queue() # queue to hold new frames for processing
        self.latest = None
        self.lock = threading.Lock() # prevent multiple threads from updating latest at the same time
        self.running = False 
        self.thread = None  


    def start(self):
        """Start watching directory"""
        self.running = True
        self.thread = threading.Thread(target=self.watch_loop, daemon=True)
        self.thread.start()

        logging.info("[CameraReader] Watching: %s", self.camera_output_dir)

    def stop(self):
        """Stop watching directory"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)

    def get_latest(self) -> str | None:
        """Get the latest frame path"""
        with self.lock:
            return self.latest
        
    def get_all_new_frames(self) -> list[str]:
        """Get all new frame paths since last call"""
        frames = []
        while not self.frame_queue.empty():
            try:
                frames.append(self.frame_queue.get_nowait())
            except queue.Empty:
                break
        return frames
    
    def wait_for_next_frame(self, timeout: float = 5.0) -> str | None:
        """Blocks until the new frame is available."""
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None
        
    def watch_loop(self):
        """
        Watch the camera output directory for new frames and add them to the queue.
        This runs in a separate thread.
        """
        while self.running:
            if self.camera_output_dir.exists():
                for frame in sorted(self.camera_output_dir.glob("frame_*.png")):
                    if frame.name not in self.seen:
                        self.seen.add(frame.name)
                        self.frame_queue.put(str(frame))
                        with self.lock:
                            self.latest = str(frame)
                        logging.debug("[CameraReader] New frame: %s", frame.name)
            time.sleep(self.frame_interval)