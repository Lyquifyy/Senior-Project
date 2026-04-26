"""
SUMO + CARLA dashboard.

Starts a CARLA/SUMO co-simulation (via SUMO/core) in a background thread with
cameras enabled, patches traci.simulationStep so every tick emits a telemetry
snapshot to all connected Socket.IO clients, and serves per-camera MJPEG
streams at /camera/<tls_id>.
"""

from argparse import Namespace
import atexit
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path

import cv2
from flask import Flask, Response, abort, jsonify, render_template
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
import traci

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


app = Flask(__name__)
load_dotenv()

secret_key = os.getenv("SEC_KEY")
if secret_key is None:
    raise RuntimeError("SEC_KEY must be set in the environment or .env file.")
app.config["SECRET_KEY"] = secret_key

_cors_env = os.getenv("CORS_ORIGINS", "http://127.0.0.1:5000")
CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]
socketio = SocketIO(app, cors_allowed_origins=CORS_ORIGINS)

# ---------------------------------------------------------------------------
# Simulation config (env-overridable)
# ---------------------------------------------------------------------------
TARGET_TLS    = os.getenv("TARGET_TLS", "238")
SCENARIO      = os.getenv("SCENARIO", "Carbon-Emission-Traffic")
# Dashboard telemetry broadcast rate. CARLA ticks at 20 Hz which is unreadable
# on the UI — we keep only the latest snapshot and emit at this cadence.
BROADCAST_HZ  = float(os.getenv("BROADCAST_HZ", "1.0"))
BROADCAST_INTERVAL = 1.0 / max(BROADCAST_HZ, 0.1)
# Camera labels are now compass-direction based: carla_sync picks the best 4-way
# intersection group at runtime and labels each camera by its angular position
# relative to the group centroid. (Legacy numeric CARLA actor IDs weren't stable
# across sessions.) If a junction has < 4 lights or unusual geometry, some tabs
# may show "Waiting for CARLA" indefinitely.
CAMERA_TLS_IDS = os.getenv("CAMERA_TLS_IDS", "east,north,west,south")
DEFAULT_CAM   = os.getenv("DEFAULT_CAMERA", CAMERA_TLS_IDS.split(",")[0].strip())

LANE_DIRECTION = {
    "-4_3":  "north", "-4_4":  "north",
    "-69_4": "south", "-69_3": "south",
    "-24_3": "east",  "24_4":  "east",
    "-23_4": "west",  "23_3":  "west",
}
PHASE_MAP = {"G": "green", "g": "green", "Y": "yellow", "y": "yellow", "R": "red", "r": "red"}
COLOUR_PRIORITY = {"green": 2, "yellow": 1, "red": 0}

sim_queue: queue.Queue = queue.Queue(maxsize=60)
latest_data: dict = {
    "step":           0,
    "intersection":   TARGET_TLS,
    "co2":            0,     # g/s
    "avg_wait_time":  0,     # seconds
    "cars":           {"north": 0, "south": 0, "east": 0, "west": 0},
    "lights":         {"north": "red", "south": "red", "east": "red", "west": "red"},
    "total_vehicles": 0,
}
data_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Snapshot builder (reads live TraCI state — no file I/O)
# ---------------------------------------------------------------------------
def _build_snapshot() -> dict:
    cars          = {"north": 0, "south": 0, "east": 0, "west": 0}
    total_co2     = 0.0
    total_wait    = 0.0
    vehicle_count = 0

    for lane_id, direction in LANE_DIRECTION.items():
        try:
            veh_ids = traci.lane.getLastStepVehicleIDs(lane_id)
        except traci.exceptions.TraCIException:
            continue
        cars[direction] += len(veh_ids)
        for vid in veh_ids:
            vehicle_count += 1
            try:
                total_co2  += traci.vehicle.getCO2Emission(vid)
                total_wait += traci.vehicle.getWaitingTime(vid)
            except traci.exceptions.TraCIException:
                pass

    lights = {d: "red" for d in ("north", "south", "east", "west")}
    try:
        state = traci.trafficlight.getRedYellowGreenState(TARGET_TLS)
        links = traci.trafficlight.getControlledLinks(TARGET_TLS)
        for i, link_group in enumerate(links):
            if i >= len(state):
                break
            colour = PHASE_MAP.get(state[i], "red")
            for (from_lane, _to, _via) in link_group:
                direction = LANE_DIRECTION.get(from_lane)
                if direction:
                    if COLOUR_PRIORITY.get(colour, 0) > COLOUR_PRIORITY.get(lights[direction], 0):
                        lights[direction] = colour
    except traci.exceptions.TraCIException:
        pass

    return {
        "step":           int(traci.simulation.getTime()),
        "intersection":   TARGET_TLS,
        # TraCI returns mg/s; convert to g/s and round to a whole number so
        # the dashboard reads cleanly (kg/s would be a tiny fractional value).
        "co2":            int(round(total_co2 / 1000)),
        "avg_wait_time":  int(round((total_wait / vehicle_count) if vehicle_count else 0.0)),
        "cars":           cars,
        "lights":         lights,
        "total_vehicles": vehicle_count,
    }


# ---------------------------------------------------------------------------
# TraCI hook: wrap simulationStep so every tick produces a dashboard snapshot
# ---------------------------------------------------------------------------
_original_step = traci.simulationStep

def _patched_step(*args, **kwargs):
    result = _original_step(*args, **kwargs)
    try:
        snapshot = _build_snapshot()
        sim_queue.put_nowait(snapshot)
    except Exception:
        logger.warning("_build_snapshot failed — snapshot dropped", exc_info=True)
    return result

traci.simulationStep = _patched_step


# ---------------------------------------------------------------------------
# Point at SUMO/core and load the co-sim runner
# ---------------------------------------------------------------------------
_SUMO_DIR = Path(__file__).resolve().parent.parent / "SUMO"
if not _SUMO_DIR.is_dir():
    raise RuntimeError(f"SUMO directory not found: {_SUMO_DIR}")
sys.path.insert(0, str(_SUMO_DIR))
if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

from run_simulation import load_scenario_config, run_carla_mode  # noqa: E402

# Where FrameConsumer writes annotated JPEGs. Keep in sync with
# args.camera_output_dir in _build_args(). Per-camera subdirs:
#   <CAMERA_OUTPUT_DIR>/consumer/camera_<tls_id>/frame_*.jpg
CAMERA_OUTPUT_DIR = Path(__file__).resolve().parent / "camera_output"
CAMERA_CONSUMER_DIR = CAMERA_OUTPUT_DIR / "consumer"


# ---------------------------------------------------------------------------
# Background: telemetry broadcaster
# ---------------------------------------------------------------------------
def broadcast_loop() -> None:
    while True:
        data = sim_queue.get()
        try:
            while True:
                data = sim_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            with data_lock:
                latest_data.update(data)
            socketio.emit("simulation_update", data)
        except Exception:
            logger.exception("broadcast_loop emit error — continuing")
        # Pace the broadcast so humans can actually read the values.
        # Sim continues to tick at full speed server-side; we just skip
        # intermediate snapshots and always emit the most recent one.
        time.sleep(BROADCAST_INTERVAL)


# ---------------------------------------------------------------------------
# Background: CARLA co-sim driver
# ---------------------------------------------------------------------------
def _build_args() -> Namespace:
    """Mirror run_simulation.py's argparse defaults with cameras enabled."""
    # Optional: CAMERA_TARGET_XY="x,y" pins the camera group to a specific
    # CARLA-world coordinate. Unset → carla_sync auto-picks the best 4-way group.
    target_xy_env = os.getenv("CAMERA_TARGET_XY", "").strip()
    target_xy = None
    if target_xy_env:
        try:
            parts = [float(p.strip()) for p in target_xy_env.split(",")]
            if len(parts) == 2:
                target_xy = tuple(parts)
        except ValueError:
            logger.warning("Ignoring malformed CAMERA_TARGET_XY=%r", target_xy_env)

    return Namespace(
        scenario=SCENARIO,
        mode="carla",
        tls_id=None,
        sim_end=500,
        heavy_co2=0.3,
        threshold=250,
        sumo_gui=False,
        carla_host=os.getenv("CARLA_HOST", "127.0.0.1"),
        carla_port=int(os.getenv("CARLA_PORT", "2000")),
        sumo_host=None,
        sumo_port=None,
        step_length=0.05,
        client_order=1,
        sync_vehicle_lights=False,
        sync_vehicle_color=False,
        sync_vehicle_all=False,
        tls_manager="sumo",
        enable_traffic_control=True,
        baseline=False,
        enable_camera=True,
        # camera_tls_id(s) are now ignored by carla_sync (geometry-driven).
        # Kept here only because other code may read them via getattr().
        camera_tls_id=DEFAULT_CAM,
        camera_tls_ids=CAMERA_TLS_IDS,
        camera_target_xy=target_xy,
        camera_output_dir=str(Path(__file__).resolve().parent / "camera_output"),
        # Camera placement knobs — tune without editing code:
        #   CAMERA_HEIGHT_M        metres above the TL pole base (default 8)
        #   CAMERA_FORWARD_M       shift along camera's facing direction
        #                          (+ = into the approach, − = into the intersection)
        #   CAMERA_LATERAL_M       sideways shift perpendicular to camera's facing
        #                          (+ = camera's right, − = camera's left;
        #                           try a few metres to centre over the lane set)
        #   CAMERA_PITCH_DEG       downward pitch (−20 = moderate, −30 = steep)
        #   CAMERA_YAW_OFFSET_DEG  add to TL yaw (0 / 90 / 180 / 270 depending on map)
        camera_height_m=float(os.getenv("CAMERA_HEIGHT_M", "8.0")),
        camera_forward_m=float(os.getenv("CAMERA_FORWARD_M", "0.0")),
        camera_lateral_m=float(os.getenv("CAMERA_LATERAL_M", "0.0")),
        camera_pitch_deg=float(os.getenv("CAMERA_PITCH_DEG", "-20.0")),
        camera_yaw_offset_deg=float(os.getenv("CAMERA_YAW_OFFSET_DEG", "0.0")),
        # ROI debug rectangle is for model tuning — off by default on the
        # dashboard. Set CAMERA_SHOW_ROI=1 to re-enable.
        camera_show_roi=os.getenv("CAMERA_SHOW_ROI", "0").lower() in ("1", "true", "yes"),
        debug=False,
    )


def _reset_carla_sync_mode() -> None:
    """Best-effort cleanup so a crashed dashboard doesn't leave CARLA stuck.

    The co-sim runs in a daemon thread, so if the main process exits abruptly
    the thread's own finally-block never runs and CARLA stays in synchronous
    mode with no ticker. Reset settings here via a fresh client connection.
    """
    try:
        import carla
        host = os.getenv("CARLA_HOST", "127.0.0.1")
        port = int(os.getenv("CARLA_PORT", "2000"))
        client = carla.Client(host, port)
        client.set_timeout(2.0)
        world = client.get_world()
        settings = world.get_settings()
        if settings.synchronous_mode:
            settings.synchronous_mode = False
            settings.fixed_delta_seconds = None
            world.apply_settings(settings)
            logger.info("atexit: reset CARLA synchronous_mode=False")
    except Exception as exc:
        # CARLA may not be reachable at exit — that's fine, nothing to do.
        logger.debug("atexit CARLA reset skipped: %s", exc)


atexit.register(_reset_carla_sync_mode)


def carla_driver() -> None:
    try:
        args = _build_args()
        scenario_dir, config = load_scenario_config(SCENARIO)
        logger.info("Starting CARLA co-sim: scenario=%s, cameras=%s", SCENARIO, CAMERA_TLS_IDS)
        run_carla_mode(scenario_dir, config, args)
    except Exception:
        logger.exception("CARLA co-sim driver thread crashed")


# ---------------------------------------------------------------------------
# MJPEG camera streams
# ---------------------------------------------------------------------------
_BOUNDARY = b"--frame"
_JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "70"))
_STREAM_FPS   = float(os.getenv("STREAM_FPS", "10"))
_PLACEHOLDER_BGR = None


def _placeholder_frame() -> bytes:
    """BGR JPEG bytes shown when no camera frame is available yet."""
    global _PLACEHOLDER_BGR
    if _PLACEHOLDER_BGR is None:
        import numpy as np
        img = np.zeros((256, 256, 3), dtype=np.uint8)
        cv2.putText(img, "Waiting for CARLA", (20, 128),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1, cv2.LINE_AA)
        _PLACEHOLDER_BGR = img
    ok, buf = cv2.imencode(".jpg", _PLACEHOLDER_BGR, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    return buf.tobytes() if ok else b""


def _latest_jpeg_for(tls_id: str) -> Path | None:
    """Newest .jpg in <consumer_dir>/camera_<tls_id>/, or None if empty/missing."""
    cam_dir = CAMERA_CONSUMER_DIR / f"camera_{tls_id}"
    if not cam_dir.is_dir():
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    try:
        with os.scandir(cam_dir) as it:
            for entry in it:
                if not entry.name.endswith(".jpg"):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = Path(entry.path)
    except OSError:
        return None
    return newest


def _mjpeg_generator(tls_id: str):
    """Yield multipart JPEG frames for `tls_id` by tailing the newest annotated
    file produced by FrameConsumer in <CAMERA_OUTPUT_DIR>/consumer/camera_<tls_id>/.

    Polls the directory each interval; re-sends a frame only when the file
    path or mtime actually changes. Shows a placeholder until the first
    annotated frame lands on disk.
    """
    interval = 1.0 / max(_STREAM_FPS, 1.0)
    last_sent: tuple[str, float] | None = None

    while True:
        loop_start = time.time()

        jpeg_bytes: bytes | None = None
        newest = _latest_jpeg_for(tls_id)
        if newest is not None:
            try:
                mtime = newest.stat().st_mtime
            except OSError:
                mtime = -1.0
            signature = (str(newest), mtime)
            if signature != last_sent:
                try:
                    jpeg_bytes = newest.read_bytes()
                    last_sent = signature
                except OSError:
                    jpeg_bytes = None

        if jpeg_bytes is None and last_sent is None:
            # Nothing on disk yet — show the placeholder so the browser knows
            # the stream is alive.
            jpeg_bytes = _placeholder_frame()

        if jpeg_bytes is not None:
            yield (
                _BOUNDARY + b"\r\n" +
                b"Content-Type: image/jpeg\r\n" +
                f"Content-Length: {len(jpeg_bytes)}\r\n\r\n".encode("ascii") +
                jpeg_bytes + b"\r\n"
            )

        elapsed = time.time() - loop_start
        if elapsed < interval:
            time.sleep(interval - elapsed)


@app.route("/camera/<tls_id>")
def camera_stream(tls_id: str):
    configured = {x.strip() for x in CAMERA_TLS_IDS.split(",") if x.strip()}
    on_disk = {p.name.removeprefix("camera_")
               for p in CAMERA_CONSUMER_DIR.glob("camera_*") if p.is_dir()}
    # Accept either a pre-configured tab OR any camera folder we can see.
    if tls_id not in configured and tls_id not in on_disk:
        abort(404)
    return Response(
        _mjpeg_generator(tls_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/cameras")
def camera_list():
    """List configured cameras and which have annotated files on disk."""
    on_disk = []
    if CAMERA_CONSUMER_DIR.is_dir():
        for p in sorted(CAMERA_CONSUMER_DIR.glob("camera_*")):
            if p.is_dir() and any(p.glob("*.jpg")):
                on_disk.append(p.name.removeprefix("camera_"))
    return jsonify({
        "configured": [x.strip() for x in CAMERA_TLS_IDS.split(",") if x.strip()],
        "on_disk":    on_disk,
        "default":    DEFAULT_CAM,
        "source_dir": str(CAMERA_CONSUMER_DIR),
    })


# ---------------------------------------------------------------------------
# HTTP + Socket.IO
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    # Tabs are populated client-side from /cameras once the co-sim starts
    # writing frames to disk.
    return render_template("index.html")


@app.route("/get", methods=["GET"])
def get_data():
    with data_lock:
        return jsonify(latest_data)


@socketio.on("connect")
def handle_connect():
    logger.info("Client connected")
    with data_lock:
        emit("simulation_update", latest_data)


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("Client disconnected")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("SMART INTERSECTION DASHBOARD — CARLA co-sim mode")
    logger.info("Dashboard : http://localhost:5000")
    logger.info("Scenario  : %s", SCENARIO)
    logger.info("Cameras   : %s", CAMERA_TLS_IDS)
    logger.info("=" * 60)

    threading.Thread(target=broadcast_loop, daemon=True).start()
    threading.Thread(target=carla_driver,   daemon=True).start()

    socketio.run(app, debug=False, use_reloader=False, host="0.0.0.0", port=5000)
