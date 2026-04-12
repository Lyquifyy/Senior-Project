import os
import cv2
import numpy as np
from skimage.feature import hog
import matplotlib.pyplot as plt
import joblib
import warnings
warnings.filterwarnings('ignore')


IMG_SIZE = (64, 64)

MODEL_PATH = "C:/Users/clair/Senior-Project/models/vehicle_svm_pipeline.pkl"
ENCODER_PATH = "C:/Users/clair/Senior-Project/models/vehicle_label_encoder.pkl"


def preprocess_image_for_prediction(image_path, bounding_box=None):

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not load image: {image_path}")
    
    # Crop if bounding box exists
    if bounding_box is not None:
        left, top, right, bottom = bounding_box
        crop = img[top:bottom, left:right]
    else:
        crop = img
    
    if crop.size == 0:
        raise ValueError("Empty crop from bounding box")
    
    crop = cv2.resize(crop, IMG_SIZE)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    hog_features = hog(
        gray,
        orientations=9,
        pixels_per_cell=(8, 8),
        cells_per_block=(2, 2),
        block_norm='L2-Hys'
    )
    
    return hog_features.reshape(1, -1)


def load_models():

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")
    if not os.path.exists(ENCODER_PATH):
        raise FileNotFoundError(f"Encoder file not found: {ENCODER_PATH}")
    
    pipeline = joblib.load(MODEL_PATH)
    label_encoder = joblib.load(ENCODER_PATH)

    return pipeline, label_encoder


def predict_single_image(image_path, pipeline, label_encoder,
                         bounding_box=None, show_result=True):

    try:
        features = preprocess_image_for_prediction(image_path, bounding_box)
        
        
        prediction = pipeline.predict(features)
        probabilities = pipeline.predict_proba(features)[0]
        
        predicted_class = label_encoder.inverse_transform(prediction)[0]
        confidence = np.max(probabilities)
        
        prob_dict = {
            label_encoder.classes_[i]: probabilities[i]
            for i in range(len(label_encoder.classes_))
        }
        
        if show_result:
            display_prediction_result(
                image_path, predicted_class, confidence,
                prob_dict, bounding_box
            )
        
        return predicted_class, confidence, prob_dict
        
    except Exception as e:
        print(f"Error predicting {image_path}: {str(e)}")
        return None, None, None


def display_prediction_result(image_path, predicted_class, confidence,
                              probabilities, bounding_box=None):

    img = cv2.imread(image_path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
    if bounding_box:
        left, top, right, bottom = bounding_box
        cv2.rectangle(img, (left, top), (right, bottom), (0, 255, 0), 2)
    
    plt.figure(figsize=(12, 5))
    
    # Image
    plt.subplot(1, 2, 1)
    plt.imshow(img)
    plt.title(f"Prediction: {predicted_class}\nConfidence: {confidence:.2%}")
    plt.axis('off')
    
    # Probabilities
    plt.subplot(1, 2, 2)
    classes = list(probabilities.keys())
    probs = list(probabilities.values())
    
    sorted_idx = np.argsort(probs)[::-1]
    classes_sorted = [classes[i] for i in sorted_idx[:5]]
    probs_sorted = [probs[i] for i in sorted_idx[:5]]
    
    colors = ['green' if c == predicted_class else 'gray'
              for c in classes_sorted]
    
    plt.barh(classes_sorted, probs_sorted, color=colors)
    plt.xlabel('Probability')
    plt.title('Top 5 Class Probabilities')
    plt.xlim(0, 1)
    
    plt.tight_layout()
    plt.show()


def main():

    try:
        # Load models
        pipeline, label_encoder = load_models()
        
        # Test image
        single_image_path = "C:/Users/clair/Downloads/BITVehicle/vehicle_0009841.jpg"
        
        if os.path.exists(single_image_path):
            predicted_class, confidence, probabilities = predict_single_image(
                single_image_path,
                pipeline,
                label_encoder,
                bounding_box=None,
                show_result=True
            )
            
            if predicted_class:
                print("\nPrediction Result:")
                print(f"  Image: {single_image_path}")
                print(f"  Predicted Class: {predicted_class}")
                print(f"  Confidence: {confidence:.2%}")
                
                print("\nTop 3 Probabilities:")
                sorted_probs = sorted(
                    probabilities.items(),
                    key=lambda x: x[1],
                    reverse=True
                )
                for i, (cls, prob) in enumerate(sorted_probs[:3], 1):
                    print(f"    {i}. {cls}: {prob:.2%}")
        else:
            print(f"\nTest image not found: {single_image_path}")
    
    except Exception as e:
        print(f"\nError: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure model files exist")
        print("2. Verify model was saved using pipeline")
        print("3. Check image path")



if __name__ == "__main__":
    main()