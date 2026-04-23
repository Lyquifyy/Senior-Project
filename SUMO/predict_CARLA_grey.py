import cv2
import numpy as np
import joblib
import os
from skimage.feature import hog

_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH   = os.path.join(_DIR, "vehicle_svm_pipeline_CARLAL_grey1.pkl")
ENCODER_PATH = os.path.join(_DIR, "vehicle_label_encoder_CARLAL_grey1.pkl")

IMG_SIZE = (128, 128)

CONFIDENCE_THRESHOLD = 0.40
MARGIN_THRESHOLD     = 0.15

# ---------------------------------------------------------------------------
# Feature extraction (grayscale HOG)
# ---------------------------------------------------------------------------
def extract_features(img):
    img = cv2.resize(img, IMG_SIZE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hog_features = hog(
        gray,
        orientations=9,
        pixels_per_cell=(8, 8),
        cells_per_block=(2, 2),
        block_norm="L2-Hys",
    )
    return hog_features.reshape(1, -1)


# ---------------------------------------------------------------------------
# Lazy-load singleton
# ---------------------------------------------------------------------------
_pipeline = None
_le = None

def _ensure_loaded():
    global _pipeline, _le
    if _pipeline is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
        if not os.path.exists(ENCODER_PATH):
            raise FileNotFoundError(f"Encoder not found: {ENCODER_PATH}")
        _pipeline = joblib.load(MODEL_PATH)
        _le       = joblib.load(ENCODER_PATH)


# ---------------------------------------------------------------------------
# Standalone prediction from file path
# ---------------------------------------------------------------------------
def predict_image(image_path):
    _ensure_loaded()
    img = cv2.imread(image_path)
    if img is None:
        print("Error: Could not load image.")
        return

    features = extract_features(img)
    probs = _pipeline.predict_proba(features)[0]

    class_probs = sorted(zip(_le.classes_, probs), key=lambda x: x[1], reverse=True)
    best_label, top1 = class_probs[0]
    _, top2 = class_probs[1]

    if top1 < CONFIDENCE_THRESHOLD or (top1 - top2) < MARGIN_THRESHOLD:
        result = "No vehicle"
    else:
        result = best_label

    print(f"Prediction: {result}")
    print(f"Confidence: {top1 * 100:.2f}%")
    print("Probabilities:")
    for name, prob in class_probs:
        print(f"  {name}: {prob * 100:.2f}%")

    return result, top1


# ---------------------------------------------------------------------------
# Live inference API — used by FrameConsumer during co-simulation
# ---------------------------------------------------------------------------
def preprocess_array(frame_rgb: np.ndarray, bounding_box=None) -> np.ndarray:
    img = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    if bounding_box is not None:
        left, top, right, bottom = bounding_box
        img = img[top:bottom, left:right]
        if img.size == 0:
            raise ValueError("Empty crop from bounding box")
    return extract_features(img)


def predict_array(frame_rgb: np.ndarray, bounding_box=None, top_k: int = 3) -> dict:
    """
    Run SVM inference on a live RGB numpy array.

    Returns dict with keys:
        predicted_class : str   (or "No vehicle" if below threshold)
        confidence      : float
        probabilities   : dict  {class_name: probability} top_k classes
    On error: {'predicted_class': None, 'confidence': None,
               'probabilities': {}, 'error': str}
    """
    try:
        _ensure_loaded()
        features = preprocess_array(frame_rgb, bounding_box)
        probs = _pipeline.predict_proba(features)[0]

        top_indices = np.argsort(probs)[::-1][:top_k]
        top_predictions = [(_le.classes_[i], float(probs[i])) for i in top_indices]

        best_label, top1 = top_predictions[0]
        top2 = top_predictions[1][1] if len(top_predictions) > 1 else 0.0

        if top1 < CONFIDENCE_THRESHOLD or (top1 - top2) < MARGIN_THRESHOLD:
            predicted_class = "No vehicle"
        else:
            predicted_class = best_label

        return {
            "predicted_class": predicted_class,
            "confidence":      top1,
            "probabilities":   {cls: prob for cls, prob in top_predictions},
        }
    except Exception as exc:
        return {
            "predicted_class": None,
            "confidence":      None,
            "probabilities":   {},
            "error":           str(exc),
        }


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_image = "C:/Users/clair/Downloads/CARLA_Light/Bus/vehicle_mitsubishi_fusorosa_cam71_000015.jpg"
    predict_image(test_image)
