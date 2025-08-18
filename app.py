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
LICENSE_API_URL = "https://api.lemonsqueezy.com/v1/licenses" # Base URL for license actions
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy Helper Functions ---
def validate_license_key(license_key):
    if not license_key:
        return None, "License key was not provided."
    try:
        # Use the /validate endpoint
        response = requests.post(f"{LICENSE_API_URL}/validate", data={'license_key': license_key}, timeout=15)
        data = response.json()
        if response.status_code == 200 and data.get('valid'):
            return data, None
        else:
            return None, data.get('error', 'Invalid license key.')
    except requests.exceptions.RequestException as e:
        print(f"License API Request Error: {e}")
        return None, "Could not connect to the license server."
    except ValueError:
        return None, "Received an invalid response from the license server."

def increment_license_usage(license_key):
    if not LEMONSQUEEZY_API_KEY:
        print("API Key is missing, cannot increment usage.")
        return None
    try:
        # Use the /increment endpoint
        response = requests.post(f"{LICENSE_API_URL}/increment", headers={'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'}, data={'license_key': license_key}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API Error during usage increment: {e}")
        return None

# --- THIS IS THE MISSING ROUTE ---
@app.route('/check-license', methods=['POST'])
def check_license():
    license_key = request.form.get('license_key')
    if not license_key:
        return jsonify({"message": "Please provide a license key."}), 400

    key_data, error_message = validate_license_key(license_key)
    
    if error_message:
        return jsonify({"message": f"License Error: {error_message}"}), 400

    # Extract usage details from the 'meta' object in the response
    meta = key_data.get('meta', {})
    uses = meta.get('uses', 0)
    activation_limit = meta.get('activation_limit', 10) # Default to 10 if not set
    remaining = activation_limit - uses

    return jsonify({
        "message": "License is valid.",
        "uses": uses,
        "limit": activation_limit,
        "remaining": remaining
    })

# --- Brushset Processing Function (Unchanged) ---
def process_brushset(filepath):
    base_filename = os.path.basename(filepath)
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4().hex}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    image_paths = []
    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            brushset_zip.extractall(temp_extract_dir)
            for root, _, files in os.walk(temp_extract_dir):
                for name in files:
                    if name.lower().endswith(('.png', '.jpg', '.jpeg')):
                        try:
                            img_path = os.path.join(root, name)
                            with Image.open(img_path) as img:
                                if img.width >= 1024 and img.height >= 1024 and name.lower() != 'artwork.png':
                                    image_paths.append(img_path)
                        except (IOError, SyntaxError):
                            continue
        return image_paths, None, temp_extract_dir
    except zipfile.BadZipFile:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "Error: The uploaded file is not a valid .brushset (it appears to be a corrupt zip file).", None
    except Exception as e:
        print(f"Unexpected error processing brushset '{filepath}': {e}")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "An unexpected server error occurred during file processing.", None

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
            return jsonify({"message": f"License Error: {error_message}"}), 400
        
        meta = key_data.get('meta', {})
        uses = meta.get('uses', 0)
        activation_limit = meta.get('activation_limit', 10)

        if uses >= activation_limit:
            return jsonify({"message": "This license key has reached its usage limit."}), 403

        session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
        os.makedirs(session_dir, exist_ok=True)
        
        with open(os.path.join(session_dir, 'key.txt'), 'w') as f:
            f.write(license_key)

    uploaded_file = request.files.get('brush_file')
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"message": "No file was provided in the request."}), 400

    filename = secure_filename(uploaded_file.filename)
    temp_filepath = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{filename}")
    uploaded_file.save(temp_filepath)

    images, error_msg, temp_extract_dir = process_brushset(temp_filepath)
    os.remove(temp_filepath)

    if error_msg:
        if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return jsonify({"message": error_msg}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    for img_path in images:
        shutil.move(img_path, os.path.join(session_dir, f"{uuid.uuid4().hex}.png"))
    if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)

    if is_last_file:
        final_zip_filename = f"converted_{secure_filename(session_id)}.zip"
        final_zip_path = os.path.join(UPLOAD_OADER, final_zip_filename)
        
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in os.listdir(session_dir):
                if item.endswith('.png'):
                    zf.write(os.path.join(session_dir, item), os.path.basename(item))
        
        key_path = os.path.join(session_dir, 'key.txt')
        if os.path.exists(key_path):
            with open(key_path, 'r') as f:
                key_to_increment = f.read().strip()
            increment_license_usage(key_to_increment)

        shutil.rmtree(session_dir, ignore_errors=True)

        return jsonify({
            "message": "Processing complete. Your download is ready.",
            "download_url": f"/download-zip/{final_zip_filename}"
        })

    return jsonify({"message": "File processed successfully. Awaiting next file."})

@app.route('/download-zip/<filename>')
def download_zip(filename):
    safe_filename = secure_filename(filename)
    directory = UPLOAD_FOLDER
    
    try:
        return send_from_directory(directory, safe_filename, as_attachment=True, download_name="Procreate_Stamps.zip")
    finally:
        try:
            os.remove(os.path.join(directory, safe_filename))
        except OSError as e:
            print(f"Error cleaning up zip file '{safe_filename}': {e}")

if __name__ == '__main__':
    app.run(debug=True, port=5001)
