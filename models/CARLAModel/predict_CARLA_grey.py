import cv2
import numpy as np
import joblib
from skimage.feature import hog

# =========================
# Paths
# =========================
MODEL_PATH = "C:/Users/clair/Senior-Project/models/CARLAModel/vehicle_svm_pipeline_CARLAL_grey1.pkl"
ENCODER_PATH = "C:/Users/clair/Senior-Project/models/CARLAModel/vehicle_label_encoder_CARLAL_grey1.pkl"

IMG_SIZE = (128, 128)

# =========================
# Load model & encoder
# =========================
pipeline = joblib.load(MODEL_PATH)
le = joblib.load(ENCODER_PATH)

print("Model loaded.")
print("Classes:", le.classes_)


# =========================
# Feature extraction (GRAYSCALE)
# =========================
def extract_features(img):
    # Resize
    img = cv2.resize(img, IMG_SIZE)

    # ✅ Convert to grayscale
    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # ✅ HOG on grayscale image
    hog_features = hog(
        img,
        orientations=9,
        pixels_per_cell=(8, 8),
        cells_per_block=(2, 2),
        block_norm="L2-Hys"
    )

    return hog_features.reshape(1, -1)


# =========================
# Prediction
# =========================
def predict_image(image_path):
    img = cv2.imread(image_path)

    if img is None:
        print("Error: Could not load image.")
        return

    features = extract_features(img)

    # Predict probabilities
    probs = pipeline.predict_proba(features)[0]

    class_probs = list(zip(le.classes_, probs))
    class_probs.sort(key=lambda x: x[1], reverse=True)

    best_label, top1 = class_probs[0]
    _, top2 = class_probs[1]

    # ✅ Rejection rule
    if top1 < 0.40 or (top1 - top2) < 0.15:
        result = "No vehicle"
    else:
        result = best_label

    print(f"\nPrediction: {result}")
    print(f"Confidence: {top1 * 100:.2f}%")

    print("\nProbabilities:")
    for name, prob in class_probs:
        print(f"  {name}: {prob * 100:.2f}%")

    return result, top1


# =========================
# Run test
# =========================
if __name__ == "__main__":
    #test_image = "C:/Users/clair/Downloads/CARLA_Light/SUV/vehicle_jeep_wrangler_rubicon_cam73_000208.jpg"
    test_image = "C:/Users/clair/Downloads/CARLA_Light/Bus/vehicle_mitsubishi_fusorosa_cam71_000015.jpg"

    predict_image(test_image)