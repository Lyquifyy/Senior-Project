import tensorflow as tf
import json
import numpy as np
from PIL import Image




from keras.utils import custom_object_scope
from tensorflow.keras.layers import Layer

# with custom_object_scope({'TrueDivide': tf.keras.layers.Lambda}):
#     model = tf.keras.models.load_model("bitvehicle_classifier.h5", compile=False)

#for h5 saved model
#model = tf.keras.models.load_model('bitvehicle_classifier1.h5',compile=False)
# model = tf.keras.models.load_model(
#     "bitvehicle_classifier.h5",
#     compile=False,
#     safe_mode=False
# )
#for keras saved model
model = tf.keras.models.load_model('bitvehicle_best2.keras')

with open('class_names.json', 'r') as f:
    class_names = json.load(f)

IMG_SIZE = 224

def predict_image(image_path):
    img = Image.open(image_path).convert('RGB')
    img = img.resize((IMG_SIZE, IMG_SIZE))
    img_array = np.array(img).astype('float32')
    img_array = np.expand_dims(img_array, axis=0)
    img_array = tf.keras.applications.mobilenet_v2.preprocess_input(img_array)


    predictions = model.predict(img_array, verbose=0)
    predicted_index = np.argmax(predictions[0])
    confidence = predictions[0][predicted_index]



    


    return class_names[predicted_index], confidence

if __name__ == "__main__":
    image_path = "C:/Users/clair/Downloads/BITVehicle/vehicle_0000467.jpg" # change to your image
    label, confidence = predict_image(image_path)

    print(f"Prediction: {label}")
    print(f"Confidence: {confidence:.2%}")