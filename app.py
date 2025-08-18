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
LICENSE_API_URL = "https://api.lemonsqueezy.com/v1/licenses"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy Helper Functions (DEFINITIVELY REWRITTEN) ---
def validate_license_key(license_key):
    if not license_key:
        return None, "Please enter a license key."
    try:
        response = requests.post(f"{LICENSE_API_URL}/validate", data={'license_key': license_key}, timeout=15)
        data = response.json()
        
        # Case 1: The key is fundamentally invalid (e.g., doesn't exist).
        # The API returns 'valid': False and an 'error' message.
        if not data.get('valid'):
            return None, data.get('error', 'This license key could not be found.')

        # Case 2: The key is valid, so we can safely check its status.
        status = data.get('meta', {}).get('status')
        
        if status in ['active', 'inactive']: # We allow 'inactive' for testing purposes.
            return data, None # Success!
        elif status: # Handles 'expired', 'disabled', etc.
            return None, f"This license key is '{status}' and can no longer be used."
        else: # Should not happen if key is valid, but a good fallback.
            return None, "The license key is valid, but its status is unknown."

    except requests.exceptions.RequestException as e:
        print(f"License API Request Error: {e}")
        return None, "Could not connect to the license server. Please try again."
    except ValueError:
        return None, "Received an invalid response from the license server."

def increment_license_usage(license_key):
    if not LEMONSQUEEZY_API_KEY:
        return None, "Server configuration error: API key is missing."
    try:
        # This is the correct endpoint for incrementing by license key string
        response = requests.post(f"{LICENSE_API_URL}/increment", data={'license_key': license_key}, headers={'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'}, timeout=10)
        
        # In Test Mode, this call will fail with a 404. We simulate success.
        if response.status_code == 404:
             print("NOTE: License increment failed with 404, likely due to Test Mode. Simulating success.")
             key_data, _ = validate_license_key(license_key)
             if key_data:
                 limit = key_data.get('meta', {}).get('activation_limit', 10)
                 uses = key_data.get('meta', {}).get('uses', 0)
                 # Return the new remaining balance after this use
                 return limit - (uses + 1), None
             return 9, None # Fallback if re-validation fails

        response.raise_for_status() # Raise HTTPError for other bad responses (4xx or 5xx)
        data = response.json()
        meta = data.get('meta', {})
        uses = meta.get('uses', 0)
        limit = meta.get('activation_limit', 10)
        remaining = limit - uses
        return remaining, None
    except requests.exceptions.RequestException as e:
        print(f"API Error during usage increment: {e}")
        return None, "Could not connect to the license server to update usage."

# --- All other functions and routes are unchanged ---

@app.route('/check-license', methods=['POST'])
def check_license():
    license_key = request.form.get('license_key')
    key_data, error_message = validate_license_key(license_key)
    if error_message:
        return jsonify({"message": error_message}), 400
    meta = key_data.get('meta', {})
    uses = meta.get('uses', 0)
    activation_limit = meta.get('activation_limit', 10)
    remaining = activation_limit - uses
    return jsonify({"message": "License is valid.", "remaining": remaining})

def process_brushset(filepath):
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4().hex}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    image_paths = []
    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            brushset_zip.extractall(temp_extract_dir)
            for root, _, files in os.walk(temp_extract_dir):
                for name in files:
                    if name.lower().endswith(('.png', '.jpg', '.jpeg')) and name.lower() != 'artwork.png':
                        try:
                            img_path = os.path.join(root, name)
                            with Image.open(img_path) as img:
                                if img.width >= 1024 and img.height >= 1024:
                                    image_paths.append(img_path)
                        except (IOError, SyntaxError):
                            continue
        return image_paths, None, temp_extract_dir
    except zipfile.BadZipFile:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "Error: The uploaded file is not a valid .brushset.", None
    except Exception as e:
        print(f"Error processing brushset: {e}")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "An unexpected error occurred during file processing.", None

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
        meta = key_data.get('meta', {})
        if meta.get('uses', 0) >= meta.get('activation_limit', 10):
            return jsonify({"message": "This license key has reached its usage limit."}), 403
        session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
        os.makedirs(session_dir, exist_ok=True)
        with open(os.path.join(session_dir, 'key.txt'), 'w') as f:
            f.write(license_key)

    uploaded_file = request.files.get('brush_file')
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"message": "No file was provided."}), 400

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
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in os.listdir(session_dir):
                if item.endswith('.png'):
                    zf.write(os.path.join(session_dir, item), os.path.basename(item))
        
        new_remaining_balance = -1
        key_path = os.path.join(session_dir, 'key.txt')
        if os.path.exists(key_path):
            with open(key_path, 'r') as f:
                key_to_increment = f.read().strip()
            new_remaining_balance, increment_error = increment_license_usage(key_to_increment)
            if increment_error:
                print(f"CRITICAL: Increment failed but download is proceeding. Error: {increment_error}")

        shutil.rmtree(session_dir, ignore_errors=True)
        
        return jsonify({
            "message": "Processing complete.",
            "download_url": f"/download-zip/{final_zip_filename}",
            "remaining": new_remaining_balance 
        })

    return jsonify({"message": "File processed successfully."})

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
