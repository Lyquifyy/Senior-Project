"""
Camera output frame reader for CARLA traffic light camera data.

Provides a simple queueing interface for live camera frames and a multi-camera
feeder for applications that need to collect frames from multiple traffic light cameras.
"""

import queue
import threading
import logging
from typing import Optional

import numpy as np


class FrameFeeder:
    """Receives numpy frame arrays from a single TrafficLightCamera."""

    def __init__(self, maxsize: int = 20):
        self._queue = queue.Queue(maxsize=maxsize)
        self._latest: Optional[tuple[str, np.ndarray]] = None
        self._lock = threading.Lock()

    def on_frame(self, tls_id: str, frame: np.ndarray):
        with self._lock:
            self._latest = (tls_id, frame)
        if self._queue.full():
            try:
                self._queue.get_nowait()
                logging.debug("[FrameFeeder] Queue full - dropped oldest frame from camera %s", tls_id)
            except queue.Empty:
                pass
        self._queue.put((tls_id, frame))
        logging.debug("[FrameFeeder] Queued frame from camera %s - queue size: %d", tls_id, self._queue.qsize())

    def get_latest(self) -> Optional[tuple[str, np.ndarray]]:
        with self._lock:
            return self._latest

    def get_all_new_frames(self) -> list[tuple[str, np.ndarray]]:
        frames = []
        while not self._queue.empty():
            try:
                frames.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return frames


class MultiCameraFeeder:
    """Collects frames from multiple TrafficLightCamera instances."""

    def __init__(self, maxsize: int = 20):
        self._feeders: dict[str, FrameFeeder] = {}
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def on_frame(self, tls_id: str, frame: np.ndarray):
        with self._lock:
            if tls_id not in self._feeders:
                self._feeders[tls_id] = FrameFeeder(maxsize=self._maxsize)
                logging.info("[MultiCameraFeeder] Registered new camera: %s", tls_id)
        self._feeders[tls_id].on_frame(tls_id, frame)

    def get_latest(self) -> dict[str, Optional[tuple[str, np.ndarray]]]:
        with self._lock:
            feeders = dict(self._feeders)
        return {cam_id: feeder.get_latest() for cam_id, feeder in feeders.items()}

    def get_all_new_frames(self) -> dict[str, list[tuple[str, np.ndarray]]]:
        with self._lock:
            feeders = dict(self._feeders)
        return {cam_id: feeder.get_all_new_frames() for cam_id, feeder in feeders.items()}
