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
# This is the correct, separate endpoint for the License API
LICENSE_API_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy Helper Functions (REVISED FOR LICENSE API) ---
def validate_license_key(license_key):
    """
    Validates a license key using the dedicated Lemon Squeezy License API.
    """
    if not license_key:
        return None, "License key was not provided."

    # The License API expects a POST request with form data
    payload = {'license_key': license_key}
    
    try:
        response = requests.post(LICENSE_API_URL, data=payload, timeout=15)
        
        # The License API does not require bearer token authentication
        # It validates the key directly.
        
        data = response.json()

        if response.status_code == 200 and data.get('valid'):
            # Key is valid, return the response data which includes license info
            return data, None
        else:
            # Handle invalid key or other errors from the API
            error_message = data.get('error', 'Invalid license key.')
            return None, error_message

    except requests.exceptions.RequestException as e:
        # Handles network errors
        print(f"License API Request Error: {e}")
        return None, "Could not connect to the license server."
    except ValueError:
        # Handle cases where response is not valid JSON
        return None, "Received an invalid response from the license server."


def increment_license_usage(license_key):
    """
    Increments the usage count for a given license key.
    This uses a different endpoint from the main JSON:API.
    """
    if not LEMONSQUEEZY_API_KEY:
        print("API Key is missing, cannot increment usage.")
        return None

    increment_url = "https://api.lemonsqueezy.com/v1/licenses/increment"
    headers = {'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'}
    payload = {'license_key': license_key}
    
    try:
        response = requests.post(increment_url, headers=headers, data=payload, timeout=10 )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API Error during usage increment: {e}")
        return None

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

# --- Main Flask Routes (Adjusted for new API logic) ---
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
            # The License API provides its own clear error messages
            return jsonify({"message": f"License Error: {error_message}"}), 400
        
        # Check activation limit from the validation response
        uses = key_data.get('meta', {}).get('uses', 0)
        activation_limit = key_data.get('meta', {}).get('activation_limit', 999) # Default to a high number if not set

        if uses >= activation_limit:
            return jsonify({"message": "This license key has reached its usage limit."}), 403

        session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
        os.makedirs(session_dir, exist_ok=True)
        
        # Store the license key itself for incrementing later
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
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in os.listdir(session_dir):
                if item.endswith('.png'):
                    zf.write(os.path.join(session_dir, item), os.path.basename(item))
        
        key_path = os.path.join(session_dir, 'key.txt')
        if os.path.exists(key_path):
            with open(key_path, 'r') as f:
                key_to_increment = f.read().strip()
            # Increment usage after successful processing
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
