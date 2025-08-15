import os
import requests
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)

# --- Configuration ---
LEMONSQUEEZY_API_KEY = os.environ.get('LEMONSQUEEZY_API_KEY')
LEMONSQUEEZY_API_URL = "https://api.lemonsqueezy.com/v1"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy Helper Functions (FINAL, CORRECTED VERSION) ---
def validate_license_key(license_key):
    """Validates a license key by attempting to retrieve it directly."""
    if not LEMONSQUEEZY_API_KEY:
        return None, "Error: API Key is missing on the server."
    
    headers = {
        'Accept': 'application/vnd.api+json',
        'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'
    }
    
    # THIS IS THE FINAL, CORRECTED API ENDPOINT.
    # We retrieve the key directly by its value, which is its ID.
    full_url = f"{LEMONSQUEEZY_API_URL}/license-keys/{license_key}"
    
    try:
        response = requests.get(full_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if data.get('data'):
            return data['data'], None
        return None, "An unexpected error occurred while validating the key."
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None, "License key not found or is invalid."
        error_details = e.response.json()
        error_message = error_details.get('errors', [{}])[0].get('detail', 'An unknown API error occurred.')
        print(f"HTTP Error from Lemon Squeezy: {error_message}")
        return None, f"API Error: {error_message}"
        
    except requests.exceptions.RequestException as e:
        print(f"API Request Error: {e}")
        return None, "Could not connect to the license server."

def increment_license_usage(key_id):
    """Increments the usage count of a license key."""
    headers = {
        'Accept': 'application/vnd.api+json',
        'Content-Type': 'application/vnd.api+json',
        'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'
    }
    payload = {
        'data': {
            'type': 'license-keys',
            'id': str(key_id),
            'attributes': {
                'increment': 1 # This tells the API to add 1 to the usage count
            }
        }
    }
    try:
        response = requests.patch(f"{LEMONSQUEEZY_API_URL}/license-keys/{key_id}", headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API Error during usage increment: {e}")
        return None

# --- Brushset Processing Function ---
def process_brushset(filepath):
    base_filename = os.path.basename(filepath)
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{base_filename}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    image_paths = []
    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            brushset_zip.extractall(temp_extract_dir)
            for root, _, files in os.walk(temp_extract_dir):
                for name in files:
                    try:
                        img_path = os.path.join(root, name)
                        with Image.open(img_path) as img:
                            width, height = img.size
                            if width >= 1024 and height >= 1024 and name.lower() != 'artwork.png':
                                image_paths.append(img_path)
                    except (IOError, SyntaxError):
                        continue
        return image_paths, None, temp_extract_dir
    except zipfile.BadZipFile:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "Error: The uploaded file is not a valid .brushset (corrupt zip).", None
    except Exception as e:
        print(f"Error processing brushset: {e}")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "An unexpected error occurred during file processing.", None

# --- Main Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/convert-single', methods=['POST'])
def convert_single():
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    is_first_file = request.form.get('is_first_file') == 'true'
    is_last_file = request.form.get('is_last_file') == 'true'

    if is_first_file:
        key_data, error_message = validate_license_key(license_key)
        if error_message:
            return jsonify({"message": error_message}), 400
        
        key_attributes = key_data.get('attributes', {})
        if key_attributes.get('status') == 'inactive':
            return jsonify({"message": "This license key is inactive."}), 403
        
        activation_limit = key_attributes.get('activation_limit')
        uses = key_attributes.get('uses', 0)

        if activation_limit is not None and uses >= activation_limit:
            return jsonify({"message": "This license key has reached its activation limit."}), 403

        session_dir = os.path.join(UPLOAD_FOLDER, session_id)
        os.makedirs(session_dir, exist_ok=True)
        
        with open(os.path.join(session_dir, 'key_id.txt'), 'w') as f:
            f.write(str(key_data['id']))

    uploaded_file = request.files.get('brush_file')
    if not uploaded_file:
        return jsonify({"message": "No file provided."}), 400

    filename = secure_filename(uploaded_file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    uploaded_file.save(filepath)

    images, error_msg, temp_dir = process_brushset(filepath)
    os.remove(filepath)

    if error_msg:
        if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"message": error_msg}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, session_id)
    for img_path in images:
        shutil.move(img_path, session_dir)
    if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)

    if is_last_file:
        final_zip_filename = f"{session_id}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in os.listdir(session_dir):
                if item.endswith('.png'):
                    zf.write(os.path.join(session_dir, item), item)
        
        with open(os.path.join(session_dir, 'key_id.txt'), 'r') as f:
            key_id_to_increment = f.read().strip()
        
        increment_license_usage(key_id_to_increment)

        shutil.rmtree(session_dir, ignore_errors=True)

        return jsonify({
            "message": "Processing complete.",
            "download_url": f"/download-zip/{final_zip_filename}"
        })

    return jsonify({"message": "File processed successfully."})

@app.route('/download-zip/<filename>')
def download_zip(filename):
    safe_filename = secure_filename(filename)
    directory = UPLOAD_FOLDER
    
    try:
        return send_from_directory(directory, safe_filename, as_attachment=True)
    finally:
        try:
            os.remove(os.path.join(directory, safe_filename))
        except OSError as e:
            print(f"Error cleaning up zip file {safe_filename}: {e}")

if __name__ == '__main__':
    app.run(debug=True)
