import os
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from PIL import Image
import warnings
from sklearn.utils.class_weight import compute_class_weight
warnings.filterwarnings('ignore')


IMAGE_FOLDER = "C:/Users/clair/Downloads/BITVehicle" 
LABEL_FILE = "VehicleInfo.xlsx"  
IMG_SIZE = 224  
BATCH_SIZE = 32
EPOCHS = 30




# Load labels
def load_from_excel():
    possible_files = ['labels.csv', 'labels.xlsx', 'VehicleInfo.csv', 'BITVehicle_labels.csv', 'VehicleInfo.csv','VehicleInfo.xlsx']
    
    for file in possible_files:
        file_path = os.path.join(IMAGE_FOLDER, file)
        
        if os.path.exists(file_path):
            print(f"Found label file: {file_path}")
            
            if file.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:  
                df = pd.read_excel(file_path)
            
            print(df.head())
            
            return df

    # for file in possible_files:
    #     if os.path.exists(IMAGE_FOLDER, file):
    #         print(f"Found label file: {file}")
    #         if file.endswith('.csv'):
    #             df = pd.read_csv(file)
    #         else:
    #             df = pd.read_excel(file)
    #         break
    # else:
    #     # If no file found, create a sample dataframe (REPLACE WITH YOUR ACTUAL LABELS)
    #     print("No label file found. Creating sample data structure...")
    #     image_files = [f for f in os.listdir(IMAGE_FOLDER) if f.endswith('.jpg')]
    #     # !!! IMPORTANT: Replace this with your actual labels !!!
    #     # You need to manually create a CSV with columns: 'filename', 'label'
    #     sample_labels = ['Sedan'] * len(image_files)  # THIS IS A PLACEHOLDER
    #     df = pd.DataFrame({'filename': image_files, 'label': sample_labels})
    
    # return df

# Load images
def load_images_from_folder(df):
    images = []
    labels = []
    valid_indices = []
    
    print("Loading images...")
    for idx, row in df.iterrows():
        img_path = os.path.join(IMAGE_FOLDER, row['name'])
        
        try:
            # Load and preprocess image
            img = Image.open(img_path).convert('RGB')
            img = img.resize((IMG_SIZE, IMG_SIZE))
            img_array = np.array(img)
            
            images.append(img_array)
            labels.append(row['category'])
            valid_indices.append(idx)
            
            # Progress indicator
            if len(images) % 1000 == 0:
                print(f"  Loaded {len(images)} images...")
                
        except Exception as e:
            print(f"Warning: Could not load {img_path}: {e}")
    
    print(f"Successfully loaded {len(images)} images")
    return np.array(images), np.array(labels)

# Create model based on MobileNetV2
def create_model(num_classes):
    # Load MobileNetV2
    base_model = tf.keras.applications.MobileNetV2(
        input_shape=(IMG_SIZE, IMG_SIZE, 3),
        include_top=False,
        weights='imagenet'
    )
    
    # Freeze the base model
    base_model.trainable = False
    
    # Create new model
    inputs = keras.Input(shape=(IMG_SIZE, IMG_SIZE, 3))
    
    # Data augmentation
    x = layers.RandomFlip("horizontal")(inputs)
    x = layers.RandomRotation(0.1)(x)
    x = layers.RandomZoom(0.1)(x)
    
    # Preprocessing
    x = tf.keras.applications.mobilenet_v2.preprocess_input(x)
    
    # Base model
    x = base_model(x, training=False)
    
    # Classification head
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dense(256, activation='relu')(x)    
    x = layers.BatchNormalization()(x)             
    x = layers.Dropout(0.5)(x)
    x = layers.Dense(128, activation='relu')(x)    
    x = layers.BatchNormalization()(x)               
    x = layers.Dropout(0.3)(x)
    outputs = layers.Dense(num_classes, activation='softmax')(x)
    
    model = keras.Model(inputs, outputs)
    
    return model


def main():
    print("="*50)
    print("BIT Vehicle Dataset Training")
    print("="*50)
    
    # Load labels
    df = load_from_excel()
    print(f"\nDataset info:")
    print(f"  Total images in index: {len(df)}")
    print(f"  Columns: {df.columns.tolist()}")
    if 'category' in df.columns:
        print(f"  Classes: {df['category'].nunique()}")
        print(f"  Class distribution:\n{df['category'].value_counts()}")
    
    # Load images
    X, y = load_images_from_folder(df)
    
    # Encode labels
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)
    y_categorical = tf.keras.utils.to_categorical(y_encoded)
    
    print(f"\nClass mapping:")
    for i, class_name in enumerate(le.classes_):
        print(f"  {i}: {class_name}")
    
    # Split data for training and testing
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_categorical, 
        test_size=0.2, 
        random_state=42,
        stratify=y_encoded
    )
    
    # Normalize pixel values
    # X_train = X_train.astype('float32') / 255.0
    # X_test = X_test.astype('float32') / 255.0

    X_train = X_train.astype('float32')
    X_test = X_test.astype('float32')



    print(f"\nTraining set: {X_train.shape[0]} images")
    print(f"Test set: {X_test.shape[0]} images")
    
    # Create and train model
    num_classes = len(le.classes_)
    model = create_model(num_classes)
    


# Model training optimizer
    optimizer = keras.optimizers.Adam(
        learning_rate=0.0001,  
        clipnorm=1.0           
    )

    # Compile model
    model.compile(
        optimizer=optimizer,
        loss='categorical_crossentropy',
        metrics=['accuracy']
    )

    print("✅ Model compiled with learning_rate=0.0001, clipnorm=1.0")

    
    print("\nModel summary:")
    model.summary()
    
    # Train
    print("\nStarting training...")


# GENTLER CLASS WEIGHTS - REPLACE your current calculation


    y_train_labels = np.argmax(y_train, axis=1)

# Calculate balanced weights
#     class_weights = compute_class_weight('balanced', 
#                                          classes=np.unique(y_train_labels), y=y_train_labels)


#     class_weights = np.sqrt(class_weights)
#     class_weights = class_weights / np.mean(class_weights)  # Normalize to average 1.0

#     class_weight_dict = dict(enumerate(class_weights))

    # class_weights = {
    #     0: 2.0,  # Bus      (558 images) - RARE
    #     1: 1.3,  # Microbus (883 images) - MEDIUM
    #     2: 2.2,  # Minivan  (476 images) - RAREST
    #     3: 0.4,  # Sedan    (5922 images) - COMMON
    #     4: 0.9,  # SUV      (1392 images) - MEDIUM
    #     5: 1.5   # Truck    (822 images) - RARE
    # }

    # print("📊 GENTLE class weights:")
    # class_names = ['Bus', 'Microbus', 'Minivan', 'Sedan', 'SUV', 'Truck']
    # for i, weight in enumerate(class_weights):
    #     print(f"   {class_names[i]}: {weight:.4f}")

    # Print the weights so you can see them
    # print("\n📊 Class weights applied:")
    # class_names = ['Bus', 'Microbus', 'Minivan', 'Sedan', 'SUV', 'Truck']  # Your 6 classes
    # for i, weight in enumerate(class_weights):
    #     print(f"   {class_names[i]}: {weight:.2f}")

    # Define class weights
    class_weight_dict = {
        0: 2.0,  # Bus      (558 images) - RARE
        1: 1.3,  # Microbus (883 images) - MEDIUM
        2: 2.2,  # Minivan  (476 images) - RAREST
        3: 0.4,  # Sedan    (5922 images) - COMMON
        4: 0.9,  # SUV      (1392 images) - MEDIUM
        5: 1.5   # Truck    (822 images) - RARE
    }

    print("\nClass weights applied:")
    class_names = ['Bus', 'Microbus', 'Minivan', 'Sedan', 'SUV', 'Truck']
    for i in range(6):
        print(f"   {class_names[i]}: {class_weight_dict[i]:.2f}")

    from tensorflow.keras.callbacks import ModelCheckpoint

# Create callbacks list
    callbacks = [
        # Save best model automatically
        ModelCheckpoint(
            'bitvehicle_best2.keras',
            #'bitvehicle_best.h5',        
            monitor='val_accuracy',      
            mode='max',                 
            save_best_only=True,        
            verbose=1                  
        ),
        # EarlyStopping(
        #     monitor='val_accuracy',
        #     patience=10,
        #     restore_best_weights=True,
        #     verbose=1
        # )
    

]

    history = model.fit(
        X_train, y_train,
        batch_size=BATCH_SIZE,
        epochs=EPOCHS,
        validation_data=(X_test, y_test),
        class_weight=class_weight_dict,  
        callbacks=callbacks,
        verbose=1
    )
    
    # Evaluate accuracy
    test_loss, test_acc = model.evaluate(X_test, y_test, verbose=0)
    print(f"\nTest accuracy: {test_acc:.4f}")
    
    # Save model
    model.save('bitvehicle_classifier2.keras')
    #print("\nModel saved as 'bitvehicle_classifier.h5'")
    
    # Save class names
    import json
    with open('class_names.json', 'w') as f:
        json.dump(le.classes_.tolist(), f)
    print("Class names saved as 'class_names.json'")
    
    return model, history, le


# def predict_image(model, class_names, image_path):
#     """Predict a single new image"""
#     img = Image.open(image_path).convert('RGB')
#     img = img.resize((IMG_SIZE, IMG_SIZE))
#     img_array = np.array(img) / 255.0
#     img_array = np.expand_dims(img_array, axis=0)
    
#     predictions = model.predict(img_array, verbose=0)
#     predicted_class_idx = np.argmax(predictions[0])
#     confidence = predictions[0][predicted_class_idx]
    
#     return class_names[predicted_class_idx], confidence


if __name__ == "__main__":
    model, history, label_encoder = main()
    





