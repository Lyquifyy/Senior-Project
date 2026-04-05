"""
Camera output frame reader

Reads frames from the camera output directory in real time and feeds them into a queue for saving and use by the model.
"""

import queue
import threading
import logging
from typing import Optional
 
import numpy as np
 
class FrameFeeder:

    """
    Receives numpy frame arrays from a single TrafficLightCamera using a callback
    and queues them for the ML model.
    """
 
    def __init__(self, maxsize: int = 20):

        self._queue = queue.Queue(maxsize=maxsize)
        self._latest: Optional[tuple[str, np.ndarray]] = None   # (tls_id, array)
        self._lock = threading.Lock()


    def on_frame(self, tls_id: str, frame: np.ndarray):
        """
        Called by TrafficLightCamera's save_worker thread each time a new
        frame is ready. Stores the frame in the queue and updates latest.
        """

        with self._lock:
            self._latest = (tls_id, frame)
 
        # If the queue is full, drop the oldest frame to make room
        if self._queue.full():
            try:
                self._queue.get_nowait()
                logging.debug("[FrameFeeder] Queue full - dropped oldest frame from camera %s", tls_id)
            except queue.Empty:
                pass
 
        self._queue.put((tls_id, frame))
        logging.debug("[FrameFeeder] Queued frame from camera %s - queue size: %d",
                     tls_id, self._queue.qsize())
        



    def get_latest(self) -> Optional[tuple[str, np.ndarray]]:
        """
        Returns the most recently received frame without removing it from the queue.
        Nonblocking. Returns None if no frames have arrived yet.
 
        """
        with self._lock:
            return self._latest
 
    def get_all_new_frames(self) -> list[tuple[str, np.ndarray]]:
        """
        Returns every frame that has arrived since the last call to this function,
        in the order they were received. empties the queue completely and returns an empty list if nothing new has arrived.
        """
        frames = []
        while not self._queue.empty():
            try:
                frames.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return frames

    
 
class MultiCameraFeeder:
    """
    Collects frames from multiple TrafficLightCamera instances into a single
    interface. Each camera calls the same on_frame callback; frames are stored
    according to camera so the model can look at any or all of them.
 
    """
 
    def __init__(self, maxsize: int = 20):
 
        self._feeders: dict[str, FrameFeeder] = {}
        self._maxsize = maxsize
        self._lock = threading.Lock()
 
    def on_frame(self, tls_id: str, frame: np.ndarray):
        """
        Single callback wired into all cameras. Routes each frame to the
        correct camera FrameFeeder, creating one dynamically if needed.
 
        """
        with self._lock:
            if tls_id not in self._feeders:
                self._feeders[tls_id] = FrameFeeder(maxsize=self._maxsize)
                logging.info("[MultiCameraFeeder] Registered new camera: %s", tls_id)
 
        self._feeders[tls_id].on_frame(tls_id, frame)
 

 
    def get_latest(self) -> dict[str, Optional[tuple[str, np.ndarray]]]:
        """
        Returns the latest frame from every camera.

        """
        with self._lock:
            feeders = dict(self._feeders)
        return {cam_id: feeder.get_latest() for cam_id, feeder in feeders.items()}
 
    def get_all_new_frames(self) -> dict[str, list[tuple[str, np.ndarray]]]:
        """
        Returns all new frames from every camera since the last call.
        """
        with self._lock:
            feeders = dict(self._feeders)
        return {cam_id: feeder.get_all_new_frames() for cam_id, feeder in feeders.items()}
    
 
