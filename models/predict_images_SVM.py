import numpy as np
import joblib
from PIL import Image
from skimage.feature import hog, local_binary_pattern
from skimage.color import rgb2gray


def extract_features(image_path, img_size=(64,64)):

    img = Image.open(image_path).convert("RGB")
    img = img.resize(img_size)
    img_array = np.array(img)

    gray = rgb2gray(img_array)

    features = []

    # Color histograms
    for i in range(3):
        hist = np.histogram(img_array[:,:,i], bins=32, range=(0,255))[0]
        features.extend(hist)

    # HOG
    hog_features = hog(
        gray,
        orientations=9,
        pixels_per_cell=(8,8),
        cells_per_block=(2,2),
        feature_vector=True
    )
    features.extend(hog_features[:500])

    # LBP
    lbp = local_binary_pattern(gray, P=8, R=1, method="uniform")
    lbp_hist = np.histogram(lbp.ravel(), bins=np.arange(0,11), range=(0,10))[0]
    features.extend(lbp_hist)

    return np.array(features)


def load_model():

    model = joblib.load("svm_bitvehicle.pkl")
    scaler = joblib.load("svm_scaler.pkl")
    pca = joblib.load("svm_pca.pkl")
    label_encoder = joblib.load("svm_label_encoder.pkl")

    return model, scaler, pca, label_encoder


def predict_image(image_path):

    model, scaler, pca, label_encoder = load_model()

    features = extract_features(image_path)
    features = features.reshape(1,-1)

    features_scaled = scaler.transform(features)
    features_pca = pca.transform(features_scaled)

    pred_idx = model.predict(features_pca)[0]
    pred_label = label_encoder.inverse_transform([pred_idx])[0]

    print("Predicted vehicle type:", pred_label)


if __name__ == "__main__":

    image_path = "test_vehicle.jpg"   
    predict_image(image_path)