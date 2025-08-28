# app.py

import os
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image

# NEW: Import the Supabase library
from supabase import create_client, Client

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =====================================================================
# NEW: SUPABASE CONNECTION SETUP
# =====================================================================
SUPABASE_URL = "https://grajrxurqeojuvrvzstz.supabase.co"
# PASTE YOUR SECRET SERVICE_ROLE KEY HERE. IT IS SAFE ON THE SERVER.
SUPABASE_KEY = "YOUR_SERVICE_ROLE_SECRET_KEY_HERE" 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY )
# =====================================================================


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

# =====================================================================
# THIS IS THE CORRECTED ROUTE
# =====================================================================
@app.route('/convert', methods=['POST'])
def convert():
    # --- 1. Get Data from the Request ---
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    is_first_file = request.form.get('is_first_file') == 'true'
    is_last_file = request.form.get('is_last_file') == 'true'
    file_index = int(request.form.get('file_index', 0))

    # --- 2. NEW: Validate the License Key with Supabase ---
    try:
        # THIS IS THE CORRECTED QUERY
        query = supabase.table('licenses').select("sessions_remaining, is_active").eq('license_key', license_key).single()
        license_response = query.execute()
        license_data = license_response.data

        if not license_data:
            return jsonify({"message": "License key not found."}), 404
        
        if not license_data.get('is_active'):
            return jsonify({"message": "License key is not active."}), 403

        if is_first_file and license_data.get('sessions_remaining', 0) <= 0:
            return jsonify({"message": "This key has no conversion sessions left."}), 403

    except Exception as e:
        print(f"Supabase validation error: {e}")
        return jsonify({"message": "Could not validate license key on the server."}), 500

    # --- 3. Handle File Upload (Unchanged) ---
    uploaded_file = request.files.get('brush_file')
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"message": "No file was provided."}), 400

    original_filename = secure_filename(uploaded_file.filename)
    brush_basename_raw = os.path.splitext(original_filename)[0]
    brush_basename = f"{file_index}-{brush_basename_raw}"

    temp_filepath = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{original_filename}")
    uploaded_file.save(temp_filepath)

    # --- 4. Process the File (Unchanged) ---
    images, error_msg, temp_extract_dir = process_brushset(temp_filepath)
    os.remove(temp_filepath)

    if error_msg:
        if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return jsonify({"message": error_msg}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    os.makedirs(session_dir, exist_ok=True)
    for i, img_path in enumerate(images):
        new_filename = f"{brush_basename}_{i + 1}.png"
        shutil.move(img_path, os.path.join(session_dir, new_filename))
        
    if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)

    # --- 5. NEW: Finalize Session (Decrement and ZIP) ---
    if is_last_file:
        new_remaining_balance = license_data['sessions_remaining']
        
        if is_first_file:
            try:
                new_remaining_balance = license_data['sessions_remaining'] - 1
                supabase.table('licenses').update({'sessions_remaining': new_remaining_balance}).eq('license_key', license_key).execute()
            except Exception as e:
                print(f"CRITICAL: Supabase update failed but download is proceeding. Error: {e}")

        final_zip_filename = f"converted_{secure_filename(session_id)}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in sorted(os.listdir(session_dir)):
                if item.endswith('.png'):
                    final_arcname = item.split('-', 1)[1] if '-' in item else item
                    zf.write(os.path.join(session_dir, item), final_arcname)
        
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
