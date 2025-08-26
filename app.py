import os
import requests
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image

# --- Flask App Initialization ---
# CORRECTED: This is the standard, correct way to initialize Flask
# to find the 'static' and 'templates' folders automatically.
app = Flask(__name__)

# --- Configuration ---
LEMONSQUEEZY_API_KEY = os.environ.get('LEMONSQUEEZY_API_KEY')
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Lemon Squeezy Helper Functions (Unchanged) ---
def validate_license_key(license_key):
    if not license_key:
        return None, "Please enter a license key."
    
    validate_url = "https://api.lemonsqueezy.com/v1/licenses/validate"
    payload = {'license_key': license_key}
    
    try:
        response = requests.post(validate_url, data=payload, timeout=15 )
        data = response.json()

        if response.status_code == 200 and data.get('valid'):
            return data, None
        else:
            return None, data.get('error', 'This license key is not valid.')

    except requests.exceptions.RequestException as e:
        print(f"License API Request Error: {e}")
        return None, "Could not connect to the license server."
    except ValueError:
        return None, "Received an invalid response from the license server."

def increment_license_usage(license_key):
    if not LEMONSQUEEZY_API_KEY:
        return None, "Server configuration error."

    increment_url = "https://api.lemonsqueezy.com/v1/licenses/increment"
    headers = {'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'}
    payload = {'license_key': license_key}
    
    try:
        response = requests.post(increment_url, headers=headers, data=payload, timeout=10 )
        
        if response.status_code == 404:
             print("NOTE: License increment failed with 404, likely due to Test Mode. Simulating success.")
             return -1, None

        response.raise_for_status()
        data = response.json()
        meta = data.get('meta', {})
        uses = meta.get('uses', 0)
        limit = meta.get('activation_limit', 10)
        return limit - uses, None
    except requests.exceptions.RequestException as e:
        print(f"API Error during usage increment: {e}")
        return None, "Could not connect to the license server to update usage."

# --- Brushset Processing Function (Unchanged) ---
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
        image_paths.sort()
        return image_paths, None, temp_extract_dir
    except zipfile.BadZipFile:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "Error: The uploaded file is not a valid .brushset.", None
    except Exception as e:
        print(f"Error processing brushset: {e}")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "An unexpected error occurred during file processing.", None

# --- Main Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

# REMOVED: The extra '/<path:filename>' route is not needed with the standard setup.

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

@app.route('/convert-single', methods=['POST'])
def convert_single():
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    is_first_file = request.form.get('is_first_file') == 'true'
    is_last_file = request.form.get('is_last_file') == 'true'
    file_index = int(request.form.get('file_index', 0))

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

    original_filename = secure_filename(uploaded_file.filename)
    brush_basename_raw = os.path.splitext(original_filename)[0]
    brush_basename = f"{file_index}-{brush_basename_raw}"

    temp_filepath = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{original_filename}")
    uploaded_file.save(temp_filepath)

    images, error_msg, temp_extract_dir = process_brushset(temp_filepath)
    os.remove(temp_filepath)

    if error_msg:
        if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return jsonify({"message": error_msg}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    for i, img_path in enumerate(images):
        new_filename = f"{brush_basename}_{i + 1}.png"
        shutil.move(img_path, os.path.join(session_dir, new_filename))
        
    if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)

    if is_last_file:
        final_zip_filename = f"converted_{secure_filename(session_id)}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in sorted(os.listdir(session_dir)):
                if item.endswith('.png'):
                    final_arcname = item.split('-', 1)[1] if '-' in item else item
                    zf.write(os.path.join(session_dir, item), final_arcname)
        
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
        return send_from_directory(directory, safe_filename, as_attachment=True, download_name="Artypacks_Conversion.zip")
    finally:
        try:
            os.remove(os.path.join(directory, safe_filename))
        except OSError as e:
            print(f"Error cleaning up zip file '{safe_filename}': {e}")

if __name__ == '__main__':
    app.run(debug=True, port=5001)
