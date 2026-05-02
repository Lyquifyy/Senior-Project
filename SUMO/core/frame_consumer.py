"""
Frame consumer for MultiCameraFeeder.

Runs in a background thread alongside the simulation. Every `poll_interval`
seconds it drains the queue from each camera, runs SVM inference on each
frame, and saves annotated images to disk showing the classification result
burned into the image itself.
"""

import logging
import os
import sys
import threading
import time

import cv2
import numpy as np
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locate predict_cosim from project root (one level above core/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from predict_CARLA_grey import predict_array
    _MODEL_AVAILABLE = True
    logger.info("[FrameConsumer] SVM model loaded from %s", _PROJECT_ROOT)
except Exception as exc:
    predict_array = None
    _MODEL_AVAILABLE = False
    logger.warning("[FrameConsumer] SVM model not available: %s", exc)

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    from ultralytics import YOLO as _YOLOClass
    _YOLO_AVAILABLE = True
except ImportError:
    _YOLOClass = None
    _YOLO_AVAILABLE = False
    logger.warning("[FrameConsumer] ultralytics not installed — install with: pip install ultralytics")

# COCO class IDs that count as vehicles
_YOLO_VEHICLE_CLASSES = {2, 3, 5, 7}  # car, motorcycle, bus, truck
_YOLO_CONFIDENCE = 0.25
_LOCK_THRESHOLD  = 0.90  # confidence at which a classification is locked in

# Emission weights per vehicle type — normalized ratios from updated emissions data.
# Small baseline = 285.90 g/mi. Medium = 341.32 g/mi → 1.194. Large = 1557.38 g/mi → 5.447.
_EMISSION_WEIGHTS: dict[str, float] = {
    "Large (Bus/Truck)":     5.447,
    "Medium (SUV/Microbus)": 1.194,
    "Small (Sedan/Minivan)": 1.0,
    "No vehicle":            0.0,
}

# ---------------------------------------------------------------------------
# Per-camera ROI — normalized (x_min, y_min, x_max, y_max) as fractions of
# image dimensions.  Only vehicles whose YOLO center falls inside this box
# will be classified.  Excludes crossing traffic at the bottom and outbound
# vehicles in far lanes.  Tune these values by looking at the saved frames.
# ---------------------------------------------------------------------------
_CAMERA_ROI: dict[str, tuple] = {
    "70": (0.15, 0.20, 0.85, 0.80),
    "71": (0.15, 0.20, 0.85, 0.80),
    "72": (0.20, 0.30, 0.70, 0.65),
    "73": (0.25, 0.30, 0.70, 0.65),
}
_DEFAULT_ROI = (0.20, 0.10, 0.80, 0.65)

_yolo_model = None

def _get_yolo():
    global _yolo_model
    if _yolo_model is None and _YOLO_AVAILABLE:
        _yolo_model = _YOLOClass("yolov8n.pt")
        logger.info("[FrameConsumer] YOLOv8n loaded for vehicle detection")
    return _yolo_model


# ---------------------------------------------------------------------------
# Annotation helper
# ---------------------------------------------------------------------------

def _annotate_frame(frame_rgb: np.ndarray, detections: list, roi: tuple | None = None) -> np.ndarray:
    """
    Burn detection + classification results onto the frame.

    detections : list of (bbox, result) where
        bbox   = (x1, y1, x2, y2) int pixel coords
        result = predict_array() return dict

    Returns a new annotated RGB array — the original is not modified.
    """
    # Shorten verbose class names so labels fit at any resolution
    _SHORT = {
        "Large (Bus/Truck)":    "Large",
        "Medium (SUV/Microbus)":"Medium",
        "Small (Sedan/Minivan)":"Small",
        "No vehicle":           "No veh",
    }

    img = frame_rgb.copy()
    h, w = img.shape[:2]

    thickness   = max(1, int(w / 400))
    label_scale = max(0.28, w / 1100)
    line_gap    = max(13, int(h * 0.028))

    # Cap banner entries so we don't overflow the image height
    MAX_ROWS = min(len(detections), 6)
    banner_h = max(line_gap, line_gap * MAX_ROWS + 6)

    # -- Top banner: one row per detected vehicle, top-3 across each row --
    cv2.rectangle(img, (0, 0), (w, banner_h), (0, 0, 0), thickness=-1)

    for idx, (_, result) in enumerate(detections[:MAX_ROWS]):
        confidence = result.get('confidence', 0.0) or 0.0
        box_color  = (0, 200, 80) if confidence >= 0.6 else (0, 165, 255) if confidence >= 0.45 else (0, 60, 220)
        probs      = result.get('probabilities', {})
        top3       = list(probs.items())

        # "V1: Large 79%  Small 15%  Med 6%"
        parts = [f"V{idx + 1}:"]
        for cls, prob in top3:
            parts.append(f"{_SHORT.get(cls, cls)} {prob * 100:.0f}%")
        row_text = "  ".join(parts)

        ly = 4 + (idx + 1) * line_gap
        cv2.putText(
            img, row_text,
            org=(6, ly),
            fontFace=cv2.FONT_HERSHEY_SIMPLEX,
            fontScale=label_scale,
            color=box_color,
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )

    # -- ROI boundary — thin yellow box showing the active filter region -----
    if roi is not None:
        rx0, ry0, rx1, ry1 = int(roi[0]*w), int(roi[1]*h), int(roi[2]*w), int(roi[3]*h)
        cv2.rectangle(img, (rx0, ry0), (rx1, ry1), (0, 220, 220), max(1, thickness))

    return img


# ---------------------------------------------------------------------------
# FrameConsumer
# ---------------------------------------------------------------------------

class FrameConsumer:
    """
    Drains a MultiCameraFeeder queue in a background thread, runs SVM
    vehicle-type inference on each frame, and saves annotated images to disk.

    Saved images have the predicted class, confidence bar, and top-3
    probabilities burned into the top of the frame so you can open any jpg
    and immediately see what the model classified.

    Parameters
    ----------
    feeder : MultiCameraFeeder
    output_dir : str
        Root directory for saved frames.  Subfolders per camera are created
        automatically.
    poll_interval : float
        Seconds between queue drain attempts.
    save_every : int
        Save 1 out of every N frames (0 = never save).
    """

    def __init__(self, feeder, output_dir: str = "frame_consumer_output",
                 poll_interval: float = 0.5, save_every: int = 5):
        self._feeder = feeder
        self._output_dir = os.path.abspath(output_dir)
        self._poll_interval = poll_interval
        self._save_every = save_every

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="FrameConsumer")

        self._total_received: dict[str, int] = {}
        self._total_saved: dict[str, int] = {}
        self._total_predicted: dict[str, int] = {}
        self._last_frame_hash: dict[str, int] = {}
        self._locked_result: dict[str, dict | None] = {}   # cam_id -> locked classification
        self._locked_center: dict[str, tuple | None] = {}  # cam_id -> (cx, cy) of locked vehicle
        self._approach_summary: dict[str, dict] = {}       # cam_id -> {vehicle_count, emission_score, dominant_type}

        os.makedirs(self._output_dir, exist_ok=True)
        logger.info("[FrameConsumer] Output directory: %s", self._output_dir)

        if not _MODEL_AVAILABLE:
            logger.warning(
                "[FrameConsumer] svm_model.pkl / scaler.pkl / label_encoder.pkl "
                "not found in %s — frames will be saved without annotations.",
                _PROJECT_ROOT,
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        logger.info(
            "[FrameConsumer] Starting — poll=%.1fs, save_every=%d frames.",
            self._poll_interval, self._save_every,
        )
        self._thread.start()

    def stop(self):
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
        self._drain()  # final drain on shutdown

    def _drain(self):
        all_frames: dict = self._feeder.get_all_new_frames()

        for cam_id, frames in all_frames.items():
            if not frames:
                continue

            if cam_id not in self._total_received:
                self._total_received[cam_id] = 0
                self._total_saved[cam_id] = 0
                self._total_predicted[cam_id] = 0
                logger.info("[FrameConsumer] First frames from camera %s", cam_id)

            for _, frame in frames:
                self._total_received[cam_id] += 1
                count = self._total_received[cam_id]

                if not isinstance(frame, np.ndarray) or frame.max() == 0:
                    if frame.max() == 0:
                        logger.warning("[FrameConsumer] Camera %s frame %d is all-zero", cam_id, count)
                    continue

                # Skip duplicate/stale frames — hash a downsampled version for speed
                frame_hash = hash(frame[::8, ::8].tobytes())
                if frame_hash == self._last_frame_hash.get(cam_id):
                    continue
                self._last_frame_hash[cam_id] = frame_hash

                # -- YOLO detection + per-vehicle SVM classification ------
                detections = []  # list of (bbox, result)
                if _MODEL_AVAILABLE:
                    yolo = _get_yolo()
                    if yolo is not None:
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        yolo_out = yolo(frame_bgr, verbose=False, conf=_YOLO_CONFIDENCE)[0]
                        h, w = frame.shape[:2]
                        roi = _CAMERA_ROI.get(str(cam_id), _DEFAULT_ROI)
                        rx0, ry0, rx1, ry1 = int(roi[0]*w), int(roi[1]*h), int(roi[2]*w), int(roi[3]*h)

                        # Collect all ROI-passing YOLO boxes, pick the largest (closest vehicle)
                        roi_boxes = []
                        for box in yolo_out.boxes:
                            if int(box.cls[0]) not in _YOLO_VEHICLE_CLASSES:
                                continue
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                            if not (rx0 <= cx <= rx1 and ry0 <= cy <= ry1):
                                continue
                            area = (x2 - x1) * (y2 - y1)
                            roi_boxes.append((area, x1, y1, x2, y2))

                        if roi_boxes:
                            # Classify only the lead vehicle (largest box)
                            roi_boxes.sort(reverse=True)
                            _, x1, y1, x2, y2 = roi_boxes[0]
                            yolo_bbox = (x1, y1, x2, y2)
                            cx_lead = (x1 + x2) // 2
                            cy_lead = (y1 + y2) // 2

                            # Reset lock if the lead vehicle has changed position
                            locked_center = self._locked_center.get(cam_id)
                            if locked_center is not None:
                                dx = cx_lead - locked_center[0]
                                dy = cy_lead - locked_center[1]
                                if (dx*dx + dy*dy) ** 0.5 > 40:
                                    self._locked_result[cam_id] = None
                                    self._locked_center[cam_id] = None

                            result = predict_array(frame, bounding_box=None)
                            conf = result.get('confidence') or 0.0
                            predicted_class = result.get('predicted_class')

                            locked = self._locked_result.get(cam_id)
                            locked_class = locked.get('predicted_class') if locked else None

                            if predicted_class and conf >= _LOCK_THRESHOLD:
                                if locked_class is None:
                                    self._locked_result[cam_id] = result
                                    self._locked_center[cam_id] = (cx_lead, cy_lead)
                                    logger.info("[FrameConsumer] Camera %s locked: %s %.1f%%",
                                                cam_id, predicted_class, conf * 100)
                                elif predicted_class != locked_class:
                                    self._locked_result[cam_id] = result
                                    self._locked_center[cam_id] = (cx_lead, cy_lead)
                                    logger.info("[FrameConsumer] Camera %s switched: %s → %s %.1f%%",
                                                cam_id, locked_class, predicted_class, conf * 100)

                            # Display locked result if one exists, otherwise use current
                            result = self._locked_result.get(cam_id) or result

                            if result.get('predicted_class') is not None:
                                detections.append((yolo_bbox, result))
                                self._total_predicted[cam_id] += 1
                                logger.info(
                                    "[FrameConsumer] Camera %s | frame %4d | "
                                    "yolo=(%d,%d,%d,%d) | %-12s  %.1f%%",
                                    cam_id, count, x1, y1, x2, y2,
                                    result['predicted_class'],
                                    result['confidence'] * 100,
                                )

                            # Update approach summary for traffic control
                            vehicle_type = result.get('predicted_class') or 'No vehicle'
                            n_vehicles = len(roi_boxes)
                            self._approach_summary[cam_id] = {
                                'vehicle_count': n_vehicles,
                                'emission_score': _EMISSION_WEIGHTS.get(vehicle_type, 1.0) * n_vehicles,
                                'dominant_type': vehicle_type,
                            }
                        else:
                            # No vehicle in ROI — clear the lock and approach summary
                            self._locked_result[cam_id] = None
                            self._locked_center[cam_id] = None
                            self._approach_summary[cam_id] = {
                                'vehicle_count': 0,
                                'emission_score': 0.0,
                                'dominant_type': None,
                            }
                    else:
                        logger.warning(
                            "[FrameConsumer] YOLO not available — skipping frame %d", count
                        )

                # -- Save annotated frame ---------------------------------
                if self._save_every > 0 and count % self._save_every == 0:
                    roi = _CAMERA_ROI.get(str(cam_id), _DEFAULT_ROI)
                    annotated = _annotate_frame(frame, detections, roi=roi)
                    self._save_frame(cam_id, count, annotated, detections)

    def _save_frame(self, cam_id: str, count: int, frame: np.ndarray, detections: list):
        cam_dir = os.path.join(self._output_dir, f"camera_{cam_id}")
        os.makedirs(cam_dir, exist_ok=True)

        if detections:
            classes = '_'.join(
                re.sub(r'[\/:*?"<>|()/ ]', '_', r.get('predicted_class', 'unknown'))
                for _, r in detections[:3]
            )
            filename = f"frame_{count:06d}_{len(detections)}veh_{classes}.jpg"
        else:
            filename = f"frame_{count:06d}_0veh.jpg"
        path = os.path.join(cam_dir, filename)

        if _PIL_AVAILABLE:
            try:
                Image.fromarray(frame.astype(np.uint8), mode="RGB").save(path)
                self._total_saved[cam_id] += 1
                logger.debug("[FrameConsumer] Saved %s", path)
            except Exception as exc:
                logger.error("[FrameConsumer] Save failed for %s: %s", path, exc)
        else:
            # Fallback: convert RGB->BGR for cv2.imwrite
            cv2.imwrite(path.replace('.jpg', '_bgr.jpg'),
                        cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            self._total_saved[cam_id] += 1

    def get_approach_summary(self, cam_id: str) -> dict:
        """Return the latest vehicle count and emission score for one camera approach.

        Safe to call from any thread; returns zeros if no data yet.
        """
        return self._approach_summary.get(str(cam_id), {
            'vehicle_count': 0,
            'emission_score': 0.0,
            'dominant_type': None,
        })

    def _log_summary(self):
        if not self._total_received:
            logger.warning("[FrameConsumer] No frames received from any camera.")
            return
        logger.info("[FrameConsumer] ---- Summary ----")
        for cam_id in sorted(self._total_received):
            logger.info(
                "[FrameConsumer]   Camera %s: %d received | %d predicted | %d saved to %s",
                cam_id,
                self._total_received[cam_id],
                self._total_predicted[cam_id],
                self._total_saved[cam_id],
                os.path.join(self._output_dir, f"camera_{cam_id}"),
            )