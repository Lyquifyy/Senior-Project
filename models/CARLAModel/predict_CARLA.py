import cv2
import numpy as np
import joblib
from skimage.feature import hog

# Set model paths
MODEL_PATH = "C:/Users/clair/Senior-Project/models/CARLAModel/vehicle_svm_pipeline_CARLAL.pkl"
ENCODER_PATH = "C:/Users/clair/Senior-Project/models/CARLAModel/vehicle_label_encoder_CARLAL.pkl"

IMG_SIZE = (128, 128)

# Load model
pipeline = joblib.load(MODEL_PATH)
le = joblib.load(ENCODER_PATH)

print("Model loaded.")
print("Classes:", le.classes_)


# Extract features from image
def extract_features(img):
    img = cv2.resize(img, IMG_SIZE)

    hog_features = []

    # color HOG extraction
    for channel in range(3):
        channel_features = hog(
            img[:, :, channel],
            orientations=9,
            pixels_per_cell=(8, 8),
            cells_per_block=(2, 2),
            block_norm="L2-Hys"
        )
        hog_features.extend(channel_features)

    return np.array(hog_features).reshape(1, -1)


# Predict image type
def predict_image(image_path):
    img = cv2.imread(image_path)

    if img is None:
        print("Error: Could not load image.")
        return

    features = extract_features(img)

    probs = pipeline.predict_proba(features)[0]

    # Pair class names with probabilities
    class_probs = list(zip(le.classes_, probs))

    # Sort highest → lowest
    class_probs.sort(key=lambda x: x[1], reverse=True)

    best_label, top1 = class_probs[0]
    _, top2 = class_probs[1]

    # Apply rejection rule
    if top1 < 0.40:# or (top1 - top2) < 0.05:
        result = "No vehicle"
    else:
        result = best_label

    print(f"\nPrediction: {result}")
    print(f"Confidence: {top1*100:.2f}")

    print("\nProbabilities:")
    for name, prob in class_probs:
        print(f"  {name}: {prob*100:.2f}")

    return result, top1


if __name__ == "__main__":
    #test_image = "c:/Users/clair/Downloads/CARLA_Light/Sedan/vehicle_toyota_prius_cam73_000533.jpg"
    test_image = "C:/Users/clair/OneDrive/Pictures/Screenshots/test10.png"

    predict_image(test_image)
    