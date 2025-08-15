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
MAX_FILE_SIZE = 50 * 1024 * 1024
MIN_IMAGE_DIMENSION = 1024
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Helper Function: Brushset Processing ---
def process_brushset(filepath):
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4().hex}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            brushset_zip.extractall(temp_extract_dir)

        image_paths = []
        for root, _, files in os.walk(temp_extract_dir):
            for name in files:
                try:
                    img_path = os.path.join(root, name)
                    with Image.open(img_path) as img:
                        width, height = img.size
                        if width >= MIN_IMAGE_DIMENSION and height >= MIN_IMAGE_DIMENSION:
                            image_paths.append(img_path)
                except (IOError, SyntaxError):
                    continue
        
        if not image_paths:
            return None, "No valid stamp images (min 1024px) found in this .brushset file."
        
        return image_paths, None

    except zipfile.BadZipFile:
        return None, "The uploaded file is not a valid .brushset file."
    except Exception as e:
        print(f"Error processing brushset: {e}")
        return None, "An unexpected error occurred while processing the brush file."
    finally:
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir, ignore_errors=True)

# --- Main Application Routes ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/convert-single', methods=['POST'])
def convert_single():
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    is_first_file = request.form.get('is_first_file') == 'true'
    is_last_file = request.form.get('is_last_file') == 'true'

    if not all([license_key, session_id]):
        return jsonify({"message": "Missing required data."}), 400

    # --- This logic runs only for the FIRST file of a session ---
    if is_first_file:
        headers = {'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}', 'Accept': 'application/vnd.api+json'}
        try:
            # 1. Get the license key details from Lemon Squeezy
            get_res = requests.get(f'https://api.lemonsqueezy.com/v1/license-keys?filter[key]={license_key}', headers=headers )
            get_res.raise_for_status()
            license_data = get_res.json().get('data')

            if not license_data:
                return jsonify({"message": "This license key does not exist."}), 404

            license_id = license_data[0]['id']
            # The 'uses' attribute tracks how many times the key has been activated.
            usage_count = license_data[0]['attributes'].get('uses', 0)

            # 2. Check if the key has credits left (our activation limit is 10)
            if usage_count >= 10:
                return jsonify({"message": "This license key has no conversion credits left."}), 403

            # 3. Increment the usage count via the API to "spend" one credit
            patch_payload = {'data': {'type': 'license-keys', 'id': license_id, 'attributes': {'uses': usage_count + 1}}}
            patch_res = requests.patch(f'https://api.lemonsqueezy.com/v1/license-keys/{license_id}', headers=headers, json=patch_payload )
            patch_res.raise_for_status()

        except requests.exceptions.RequestException as e:
            print(f"API Error: {e}")
            return jsonify({"message": "Could not validate the license key with the server."}), 500
    # --- End of first-file logic ---

    brush_file = request.files.get('brush_file')
    if not brush_file or not brush_file.filename.lower().endswith('.brushset'):
        return jsonify({"message": "A valid .brushset file is required."}), 400

    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)
    
    filepath = os.path.join(session_folder, secure_filename(brush_file.filename))
    brush_file.save(filepath)

    image_paths, error = process_brushset(filepath)
    os.remove(filepath)

    if error:
        return jsonify({"message": error}), 400

    for img_path in image_paths:
        shutil.move(img_path, session_folder)

    if is_last_file:
        final_zip_filename = f"{session_id}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        
        with zipfile.ZipFile(final_zip_path, 'w') as zipf:
            for i, item in enumerate(os.listdir(session_folder)):
                if item.lower().endswith('.png'):
                    zipf.write(os.path.join(session_folder, item), f'stamp_{i+1}.png')
        
        shutil.rmtree(session_folder, ignore_errors=True)
        
        return jsonify({
            "message": "All files processed successfully.",
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
