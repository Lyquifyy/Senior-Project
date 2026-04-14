import os
import cv2
import numpy as np
from skimage.feature import hog
import matplotlib.pyplot as plt
import joblib
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================
IMG_SIZE = (64, 64)
_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(_DIR, "svm_model.pkl")
SCALER_PATH = os.path.join(_DIR, "scaler.pkl")
ENCODER_PATH = os.path.join(_DIR, "label_encoder.pkl")

# Preprocess image
def preprocess_image_for_prediction(image_path, bounding_box=None):
   
    # Load image
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not load image: {image_path}")
    
    # If bounding box provided, crop it
    if bounding_box is not None:
        left, top, right, bottom = bounding_box
        crop = img[top:bottom, left:right]
    else:
        # If no bounding box, use the whole image
        crop = img
    
    # Skip bad crops
    if crop.size == 0:
        raise ValueError("Empty crop from bounding box")
    
    # Resize to same size as training
    crop = cv2.resize(crop, IMG_SIZE)
    
    # Convert to grayscale
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    # Extract HOG features (shape/edge features)
    hog_features = hog(
        gray,
        orientations=9,
        pixels_per_cell=(8, 8),
        cells_per_block=(2, 2),
        block_norm='L2-Hys'
    )

    # Extract HSV color histogram (color distribution features)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist_h = np.histogram(hsv[:, :, 0], bins=16, range=(0, 180))[0].astype(float)
    hist_s = np.histogram(hsv[:, :, 1], bins=16, range=(0, 256))[0].astype(float)
    hist_v = np.histogram(hsv[:, :, 2], bins=16, range=(0, 256))[0].astype(float)
    for hist in (hist_h, hist_s, hist_v):
        total = hist.sum()
        if total > 0:
            hist /= total
    color_features = np.concatenate([hist_h, hist_s, hist_v])  # 48 values

    return np.concatenate([hog_features, color_features]).reshape(1, -1)

# Load model
def load_models():

    # Check if model files exist
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")
    if not os.path.exists(SCALER_PATH):
        raise FileNotFoundError(f"Scaler file not found: {SCALER_PATH}")
    if not os.path.exists(ENCODER_PATH):
        raise FileNotFoundError(f"Encoder file not found: {ENCODER_PATH}")
    
    # Load models
    svm = joblib.load(MODEL_PATH)
    scaler = joblib.load(SCALER_PATH)
    label_encoder = joblib.load(ENCODER_PATH)
    return svm, scaler, label_encoder


def predict_single_image(image_path, svm, scaler, label_encoder, bounding_box=None, show_result=True):

    try:
        # Preprocess image
        features = preprocess_image_for_prediction(image_path, bounding_box)
        
        # Scale features
        features_scaled = scaler.transform(features)
        
        # Get prediction
        prediction = svm.predict(features_scaled)

        # Use decision_function scores + softmax since model was trained without probability=True
        scores = svm.decision_function(features_scaled)[0]
        scores_shifted = scores - scores.max()
        exp_scores = np.exp(scores_shifted)
        probabilities = exp_scores / exp_scores.sum()

        # Get class name and confidence
        predicted_class = label_encoder.inverse_transform(prediction)[0]
        inner_svc = svm.named_steps['svm']
        class_index = list(inner_svc.classes_).index(prediction[0])
        confidence = probabilities[class_index]

        # Create probability dictionary
        prob_dict = {label_encoder.classes_[i]: probabilities[i]
                    for i in range(len(label_encoder.classes_))}
        
        # Display result
        if show_result:
            display_prediction_result(image_path, predicted_class, confidence, 
                                     prob_dict, bounding_box)
        
        return predicted_class, confidence, prob_dict
        
    except Exception as e:
        print(f"Error predicting {image_path}: {str(e)}")
        return None, None, None

# def predict_multiple_images(image_paths, svm, scaler, label_encoder, bounding_boxes=None):

#     results = []
    
#     print("\n" + "=" * 60)
#     print("PREDICTING MULTIPLE IMAGES")
#     print("=" * 60)
    
#     for i, img_path in enumerate(image_paths):
#         print(f"\nProcessing {i+1}/{len(image_paths)}: {os.path.basename(img_path)}")
        
#         bbox = bounding_boxes[i] if bounding_boxes and i < len(bounding_boxes) else None
#         pred_class, confidence, probs = predict_single_image(
#             img_path, svm, scaler, label_encoder, bbox, show_result=False
#         )
        
#         if pred_class:
#             results.append({
#                 'image_path': img_path,
#                 'predicted_class': pred_class,
#                 'confidence': confidence,
#                 'probabilities': probs
#             })
#             print(f"  ✓ Predicted: {pred_class} (confidence: {confidence:.2%})")
#         else:
#             print(f"  ✗ Failed to predict")
#             results.append({
#                 'image_path': img_path,
#                 'predicted_class': None,
#                 'confidence': None,
#                 'probabilities': None
#             })
    
#     return results

def display_prediction_result(image_path, predicted_class, confidence, probabilities, bounding_box=None):
  
    # Load image
    img = cv2.imread(image_path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
    # Draw bounding box if provided
    if bounding_box:
        left, top, right, bottom = bounding_box
        cv2.rectangle(img, (left, top), (right, bottom), (0, 255, 0), 2)
    
    # Create figure
    plt.figure(figsize=(12, 5))
    
    # Show image
    plt.subplot(1, 2, 1)
    plt.imshow(img)
    plt.title(f"Prediction: {predicted_class}\nConfidence: {confidence:.2%}")
    plt.axis('off')
    
    # Show probabilities
    plt.subplot(1, 2, 2)
    classes = list(probabilities.keys())
    probs = list(probabilities.values())
    
    # Sort by probability
    sorted_idx = np.argsort(probs)[::-1]
    classes_sorted = [classes[i] for i in sorted_idx[:5]]  # Top 5
    probs_sorted = [probs[i] for i in sorted_idx[:5]]
    
    colors = ['green' if c == predicted_class else 'gray' for c in classes_sorted]
    plt.barh(classes_sorted, probs_sorted, color=colors)
    plt.xlabel('Probability')
    plt.title('Top 5 Class Probabilities')
    plt.xlim(0, 1)
    
    plt.tight_layout()
    plt.show()


# def batch_predict_and_save(image_folder, svm, scaler, label_encoder, 
#                           output_csv="predictions.csv", bounding_boxes_file=None):

#     # Get all image files
#     image_extensions = ['.jpg', '.jpeg', '.png', '.bmp']
#     image_files = []
    
#     for file in os.listdir(image_folder):
#         if any(file.lower().endswith(ext) for ext in image_extensions):
#             image_files.append(os.path.join(image_folder, file))
    
#     print(f"\nFound {len(image_files)} images in {image_folder}")
    
#     # Load bounding boxes if provided
#     bounding_boxes = {}
#     if bounding_boxes_file and os.path.exists(bounding_boxes_file):
#         import pandas as pd
#         bbox_df = pd.read_csv(bounding_boxes_file)
#         for _, row in bbox_df.iterrows():
#             bounding_boxes[row['filename']] = (
#                 int(row['left']), int(row['top']), 
#                 int(row['right']), int(row['bottom'])
#             )
#         print(f"Loaded bounding boxes for {len(bounding_boxes)} images")
    
#     # Predict all images
#     results = []
#     for img_path in image_files:
#         filename = os.path.basename(img_path)
#         bbox = bounding_boxes.get(filename, None)
        
#         pred_class, confidence, probs = predict_single_image(
#             img_path, svm, scaler, label_encoder, bbox, show_result=False
#         )
        
#         if pred_class:
#             results.append({
#                 'filename': filename,
#                 'predicted_class': pred_class,
#                 'confidence': confidence,
#                 'full_path': img_path
#             })
    
#     # Save to CSV
#     if results:
#         df = pd.DataFrame(results)
#         df.to_csv(output_csv, index=False)
#         print(f"\n✓ Predictions saved to: {output_csv}")
#         print(f"\nPrediction Summary:")
#         print(df['predicted_class'].value_counts())
#     else:
#         print("\nNo successful predictions to save")


def main():

    try:
        # Load models
        svm, scaler, label_encoder = load_models()
        
        # Predict single image
        single_image_path = os.path.join(_DIR, "pic4.png")
        #single_image_path = "C:/Users/clair/Downloads/test_car2.jpg"
        
        if os.path.exists(single_image_path):
            predicted_class, confidence, probabilities = predict_single_image(
                single_image_path, svm, scaler, label_encoder,
                bounding_box=None,  # Add bounding box if you have it
                show_result=True
            )
            
            if predicted_class:
                print(f"\nPrediction Result:")
                print(f"  Image: {single_image_path}")
                print(f"  Predicted Class: {predicted_class}")
                print(f"  Confidence: {confidence:.2%}")
                print(f"\nTop 3 Probabilities:")
                sorted_probs = sorted(probabilities.items(), key=lambda x: x[1], reverse=True)
                for i, (cls, prob) in enumerate(sorted_probs[:3], 1):
                    print(f"    {i}. {cls}: {prob:.2%}")
        else:
            print(f"\nTest image not found: {single_image_path}")
            print("Skipping single image prediction example")
        
        # Predict multiple images

        # test_folder = "C:/Users/clair/Downloads/BITVehicle/test_images"
        
        # if os.path.exists(test_folder):
        #     # Get all images in folder
        #     image_files = []
        #     for file in os.listdir(test_folder):
        #         if file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
        #             image_files.append(os.path.join(test_folder, file))
            
        #     if image_files:
        #         # Predict first 5 images
        #         results = predict_multiple_images(
        #             image_files[:5], svm, scaler, label_encoder
        #         )
                
        #         # Print summary
        #         print("\n" + "=" * 60)
        #         print("PREDICTION SUMMARY")
        #         print("=" * 60)
        #         for result in results:
        #             if result['predicted_class']:
        #                 print(f"{os.path.basename(result['image_path'])}: "
        #                       f"{result['predicted_class']} "
        #                       f"(confidence: {result['confidence']:.2%})")
        #     else:
        #         print(f"No images found in {test_folder}")
        # else:
        #     print(f"\nTest folder not found: {test_folder}")
        #     print("Skipping multiple image prediction example")
        
        # Batch predict images, csv output
 
        
        # Uncomment to use batch prediction
        # batch_predict_and_save(
        #     image_folder="C:/Users/clair/Downloads/BITVehicle/test_images",
        #     svm=svm,
        #     scaler=scaler,
        #     label_encoder=label_encoder,
        #     output_csv="vehicle_predictions.csv"
        # )
        

        
    except Exception as e:
        print(f"\nError: {str(e)}")
        print("\nTroubleshooting:")
        print("1. Make sure model files exist in the current directory")
        print("2. Verify model files were created by the training script")
        print("3. Check that image paths are correct")


# def quick_predict(image_path, bounding_box=None):

#     try:
#         svm, scaler, label_encoder = load_models()
#         return predict_single_image(image_path, svm, scaler, label_encoder, 
#                                    bounding_box, show_result=True)
#     except Exception as e:
#         print(f"Error: {str(e)}")
#         return None, None, None

# ============================================================================
# RUN MAIN FUNCTION
# ============================================================================
if __name__ == "__main__":
    # Run main prediction pipeline
    main()
    
    # Alternatively, use quick prediction:
    # quick_predict("path/to/your/image.jpg")