import os
import requests
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, send_from_directory, jsonify, after_this_request
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)

# --- Configuration ---
LEMONSQUEEZY_API_KEY = os.environ.get('LEMONSQUEEZY_API_KEY')
LEMONSQUEEZY_API_URL = "https://api.lemonsqueezy.com/v1"
CREDITS_PER_PURCHASE = 10
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_BRUSH_COUNT = 100
MIN_IMAGE_DIMENSION = 1024
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy API Helper Functions ---
def validate_license_key(license_key):
    if not LEMONSQUEEZY_API_KEY:
        raise ValueError("LEMONSQUEEZY_API_KEY is not set in environment variables.")
    headers = {'Accept': 'application/vnd.api+json', 'Content-Type': 'application/vnd.api+json', 'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'}
    params = {'license_key': license_key}
    try:
        response = requests.get(f"{LEMONSQUEEZY_API_URL}/licenses/validate", headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get('valid'):
            uses = data.get('meta', {}).get('uses', 0)
            if uses < CREDITS_PER_PURCHASE:
                return data
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Lemon Squeezy API: {e}")
    return None

def increment_license_usage(license_id):
    headers = {'Accept': 'application/vnd.api+json', 'Content-Type': 'application/vnd.api+json', 'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'}
    payload = {"data": {"type": "licenses", "id": str(license_id), "attributes": {"increment": 1}}}
    try:
        response = requests.patch(f"{LEMONSQUEEZY_API_URL}/licenses/{license_id}", headers=headers, json=payload)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error incrementing license usage: {e}")
        return False

# --- Brush Processing Function (Simplified) ---
def process_brushset(filepath):
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4()}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    extracted_image_paths = []
    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            if len(brushset_zip.infolist()) > MAX_BRUSH_COUNT * 2:
                return [], None, "Error: Brush set contains too many items."
            brushset_zip.extractall(temp_extract_dir)
        for root, dirs, files in os.walk(temp_extract_dir):
            for name in files:
                item_path = os.path.join(root, name)
                try:
                    with Image.open(item_path) as img:
                        width, height = img.size
                        if width >= MIN_IMAGE_DIMENSION and height >= MIN_IMAGE_DIMENSION:
                            # No transparency logic, just save the image as PNG
                            temp_png_path = os.path.join(temp_extract_dir, f"processed_{uuid.uuid4()}.png")
                            img.save(temp_png_path, 'PNG')
                            extracted_image_paths.append(temp_png_path)
                except (IOError, SyntaxError):
                    continue
        if not extracted_image_paths:
            return [], None, "Error: No valid stamp images found (min 1024x1024px)."
        return extracted_image_paths, temp_extract_dir, None
    except zipfile.BadZipFile:
        return [], None, "Error: The uploaded file is not a valid .brushset file."
    except Exception as e:
        print(f"Error during brushset processing: {e}")
        return [], None, "An unexpected error occurred during processing."

# --- Main Application Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/convert-single', methods=['POST'])
def convert_single():
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    brush_file = request.files.get('brush_file')
    is_last_file = request.form.get('is_last_file') == 'true'
    is_first_file = request.form.get('is_first_file') == 'true'

    if not all([license_key, session_id, brush_file]):
        return jsonify({"message": "Missing required data."}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    if is_first_file:
        license_data = validate_license_key(license_key)
        if not license_data:
            shutil.rmtree(session_dir, ignore_errors=True)
            return jsonify({"message": "Invalid or expired license key."}), 403
        with open(os.path.join(session_dir, ".valid"), "w") as f:
            f.write(str(license_data['meta']['license_key_id']))

    if not os.path.exists(os.path.join(session_dir, ".valid")):
         return jsonify({"message": "Invalid session. Please start over."}), 403

    filename = secure_filename(brush_file.filename)
    filepath = os.path.join(session_dir, filename)
    brush_file.save(filepath)

    processed_images, temp_extract_dir, error_message = process_brushset(filepath)
    
    if error_message:
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"message": error_message}), 400

    output_dir = os.path.join(session_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    for img_path in processed_images:
        shutil.move(img_path, os.path.join(output_dir, os.path.basename(img_path)))

    if temp_extract_dir and os.path.exists(temp_extract_dir):
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
    os.remove(filepath)

    if is_last_file:
        final_zip_filename = f"{session_id}.zip"
        archive_base_path = os.path.join(UPLOAD_FOLDER, session_id)
        shutil.make_archive(archive_base_path, 'zip', output_dir)
        
        with open(os.path.join(session_dir, ".valid"), "r") as f:
            license_id = f.read().strip()
        increment_license_usage(license_id)
        
        shutil.rmtree(session_dir, ignore_errors=True)

        return jsonify({"message": "Processing complete.", "download_url": f"/download-zip/{final_zip_filename}"})

    return jsonify({"message": "File processed successfully."})

@app.route('/download-zip/<filename>')
def download_zip(filename):
    safe_filename = secure_filename(filename)
    directory = UPLOAD_FOLDER
    
    @after_this_request
    def cleanup(response):
        try:
            os.remove(os.path.join(directory, safe_filename))
        except Exception as e:
            print(f"Error cleaning up zip file {safe_filename}: {e}")
        return response

    return send_from_directory(directory, safe_filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
