import os
import cv2
import numpy as np
from skimage.feature import hog
import joblib
import warnings

warnings.filterwarnings("ignore")

# Set image size
IMG_SIZE = (64, 64)

# Set model paths
MODEL_PATH = "C:/Users/clair/Senior-Project/models/vehicle_svm_pipeline.pkl"
ENCODER_PATH = "C:/Users/clair/Senior-Project/models/vehicle_label_encoder.pkl"

# preprocess image for prediction and extract features
def preprocess_image_for_prediction(img, bounding_box=None):

    # Raise error if image input is invalid 
    if img is None:
        raise ValueError("Invalid image input")

    # Set image bounding box if one is given
    if bounding_box is not None:
        left, top, right, bottom = bounding_box
        img = img[top:bottom, left:right]

    if img.size == 0:
        raise ValueError("Empty image crop")

    # Resize image to correct size and change color to gray
    img = cv2.resize(img, IMG_SIZE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Extract HOG features from image
    hog_features = hog(
        gray,
        orientations=9,
        pixels_per_cell=(8, 8),
        cells_per_block=(2, 2),
        block_norm="L2-Hys"
    )

    # Return HOG features extracted from processed image
    return hog_features.reshape(1, -1)

# load model files
def load_models():
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Model not found: {MODEL_PATH}")

    if not os.path.exists(ENCODER_PATH):
        raise FileNotFoundError(f"Encoder not found: {ENCODER_PATH}")

    pipeline = joblib.load(MODEL_PATH)
    label_encoder = joblib.load(ENCODER_PATH)

    # Return model pipeline ready to predict
    return pipeline, label_encoder


# predict single image type
def predict_single_image(image_path, pipeline, label_encoder,
                         bounding_box=None, top_k=3):

    # Read image
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not load image: {image_path}")

    # Call preprocess image function to extract features
    features = preprocess_image_for_prediction(img, bounding_box)

    # Predict vehicle type probabilities 
    probabilities = pipeline.predict_proba(features)[0]

    top_indices = np.argsort(probabilities)[::-1][:top_k]

    # Decide top 3 types based on probabilities
    top_predictions = [
        (label_encoder.classes_[i], probabilities[i])
        for i in top_indices
    ]

    predicted_class = top_predictions[0][0]
    confidence = top_predictions[0][1]

    # Return predicted class, top 3 probabilities, and confidence levels for each prediction
    return predicted_class, confidence, top_predictions


def main():
    try:
        pipeline, label_encoder = load_models()

        # Set image path to predict 
        image_path = "C:/Users/clair/Downloads/BITVehicle/vehicle_0000452.jpg"

        if not os.path.exists(image_path):
            print("Image not found:", image_path)
            return

        # Call predict single image function to predict vehicle type
        predicted_class, confidence, top_predictions = predict_single_image(
            image_path,
            pipeline,
            label_encoder,
            bounding_box=None,  
            top_k=3
        )

        # Print prediction results
        print("\nPrediction Result")
        print("-----------------")
        print(f"Image: {image_path}")
        print(f"Predicted Class: {predicted_class}")
        print(f"Confidence: {confidence * 100:.2f}%")

        print("\nTop 3 Probabilities:")
        for i, (cls, prob) in enumerate(top_predictions, 1):
            print(f"  {i}. {cls}: {prob * 100:.2f}%")

    except Exception as e:
        print("\nError:", str(e))


if __name__ == "__main__":
    main()
