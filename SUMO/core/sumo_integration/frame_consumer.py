"""
Diagnostic frame consumer for MultiCameraFeeder.

Runs in a background thread alongside the simulation. Every poll_interval
seconds it drains the queue from each camera, logs what it found, and saves
a copy of each frame to output_dir so you can visually confirm the images
are correct.

The consumer is intentionally separate from the simulation loop so it never
slows down the tick rate.
"""

import logging
import os
import threading
import time

import numpy as np

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    logging.warning(
        "[FrameConsumer] Pillow not installed; frames will be saved as .npy "
        "arrays instead of .png images.  Run: pip install Pillow"
    )

logger = logging.getLogger(__name__)


class FrameConsumer:
    """
    Drains a MultiCameraFeeder queue in a background thread.

    """

    def __init__(self, feeder, output_dir: str = "frame_consumer_output",
                 poll_interval: float = 0.5, save_every: int = 5):
        self._feeder = feeder
        self._output_dir = os.path.abspath(output_dir)
        self._poll_interval = poll_interval
        self._save_every = save_every

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="FrameConsumer")

        # Per-camera counters
        self._total_received: dict[str, int] = {}
        self._total_saved: dict[str, int] = {}

        os.makedirs(self._output_dir, exist_ok=True)
        logger.info("[FrameConsumer] Output directory: %s", self._output_dir)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        """Start the background consumer thread."""
        logger.info(
            "[FrameConsumer] Starting — polling every %.1fs, saving every %d frames.",
            self._poll_interval, self._save_every,
        )
        self._thread.start()

    def stop(self):
        """Signal the consumer to stop and wait for it to finish."""
        self._stop_event.set()
        self._thread.join(timeout=5)
        self._log_summary()

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self):
        while not self._stop_event.is_set():
            self._drain()
            time.sleep(self._poll_interval)
        # Final drain after stop() is called so no frames are missed
        self._drain()

    def _drain(self):
        """Pull all queued frames from every camera and process them."""
        all_frames: dict[str, list] = self._feeder.get_all_new_frames()

        for cam_id, frames in all_frames.items():
            if not frames:
                continue

            if cam_id not in self._total_received:
                self._total_received[cam_id] = 0
                self._total_saved[cam_id] = 0
                logger.info("[FrameConsumer] First frames received from camera %s", cam_id)

            for tls_id, frame in frames:
                self._total_received[cam_id] += 1
                count = self._total_received[cam_id]

                # -- Sanity checks -----------------------------------------
                if not isinstance(frame, np.ndarray):
                    logger.warning(
                        "[FrameConsumer] Camera %s frame %d is not a numpy array (got %s)",
                        cam_id, count, type(frame),
                    )
                    continue

                logger.debug(
                    "[FrameConsumer] Camera %s | frame %d | shape=%s dtype=%s "
                    "min=%d max=%d",
                    cam_id, count, frame.shape, frame.dtype,
                    int(frame.min()), int(frame.max()),
                )

                if frame.max() == 0:
                    logger.warning(
                        "[FrameConsumer] Camera %s frame %d is all-zero — "
                        "possible camera spawn or conversion issue.",
                        cam_id, count,
                    )

                # -- Save every Nth frame ----------------------------------
                if count % self._save_every == 0:
                    self._save_frame(cam_id, count, frame)

    def _save_frame(self, cam_id: str, count: int, frame: np.ndarray):
        """Save a single frame to disk."""
        cam_dir = os.path.join(self._output_dir, f"camera_{cam_id}")
        os.makedirs(cam_dir, exist_ok=True)

        if _PIL_AVAILABLE:
            # frame is H x W x 3 (RGB)
            try:
                img = Image.fromarray(frame.astype(np.uint8), mode="RGB")
                path = os.path.join(cam_dir, f"frame_{count:06d}.png")
                img.save(path)
                self._total_saved[cam_id] += 1
                logger.info("[FrameConsumer] Saved %s", path)
            except Exception as exc:
                logger.error(
                    "[FrameConsumer] Failed to save frame %d from camera %s: %s",
                    count, cam_id, exc,
                )
        else:
            # Fallback: save raw numpy array
            path = os.path.join(cam_dir, f"frame_{count:06d}.npy")
            np.save(path, frame)
            self._total_saved[cam_id] += 1
            logger.info("[FrameConsumer] Saved %s (numpy array)", path)

    def _log_summary(self):
        """Log final per-camera frame counts."""
        if not self._total_received:
            logger.warning(
                "[FrameConsumer] No frames were received from any camera. "
                "Check that cameras spawned correctly and the simulation ran "
                "long enough for frames to be queued."
            )
            return
        logger.info("[FrameConsumer] ---- Summary ----")
        for cam_id in sorted(self._total_received):
            logger.info(
                "[FrameConsumer]   Camera %s: %d received, %d saved to disk",
                cam_id,
                self._total_received[cam_id],
                self._total_saved[cam_id],
            )