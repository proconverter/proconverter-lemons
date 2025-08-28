import os
import requests
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image
from supabase import create_client, Client

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Supabase Configuration ---
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY')

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise ValueError("Supabase URL and Service Key must be set in environment variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

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
        # UPDATED MESSAGE
        return None, "This file seems to be corrupted or isn't a valid .brushset.", None
    except Exception as e:
        print(f"Error processing brushset: {e}")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        # UPDATED MESSAGE
        return None, "Something went wrong on our end. Please try again in a moment.", None

# --- Main Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

# --- Route to check license balance ---
@app.route('/check-license')
def check_license():
    license_key = request.args.get('license_key')
    if not license_key:
        return jsonify({"message": "License key is required."}), 400
    try:
        response = supabase.from_('licenses').select('sessions_remaining, is_active').eq('license_key', license_key).single().execute()
        if response.data is None:
            # UPDATED MESSAGE
            return jsonify({"message": "That license key wasn't found. Please check for typos."}), 404
        
        key_data = response.data
        if not key_data.get('is_active'):
            # UPDATED MESSAGE
            return jsonify({"message": "Your license isn't active yet. Please check your email."}), 403

        return jsonify({"remaining": key_data.get('sessions_remaining', 0)})

    except Exception as e:
        print(f"Check-license error: {e}")
        # This message is now handled by the frontend, but we keep a generic server error here.
        return jsonify({"message": "A server error occurred. Please try again later."}), 500

# --- FINAL VERSION: Main conversion route with transactional logic ---
@app.route('/convert', methods=['POST'])
def convert():
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    is_first_file = request.form.get('is_first_file') == 'true'
    is_last_file = request.form.get('is_last_file') == 'true'
    uploaded_file = request.files.get('brush_file')
    
    # --- Step 1: On the first file, ONLY validate the license. Do NOT decrement yet. ---
    if is_first_file:
        try:
            validation_response = supabase.from_('licenses').select('sessions_remaining, is_active').eq('license_key', license_key).single().execute()
            if validation_response.data is None:
                return jsonify({"message": "That license key wasn't found. Please check for typos."}), 404
            
            key_data = validation_response.data
            if not key_data.get('is_active'):
                return jsonify({"message": "Your license isn't active yet. Please check your email."}), 403
            if key_data.get('sessions_remaining', 0) <= 0:
                # This message is now primarily handled by the frontend's "Buy More" link.
                return jsonify({"message": "This license key has no conversions left."}), 403
        except Exception as e:
            print(f"Supabase validation error: {e}")
            return jsonify({"message": f"Something went wrong on our end. Please try again."}), 500

    # --- Step 2: Process the file as usual. ---
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"message": "No file was provided."}), 400

    original_filename = secure_filename(uploaded_file.filename)
    file_index = int(request.form.get('file_index', 0))
    brush_basename_raw = os.path.splitext(original_filename)[0]
    brush_basename = f"{file_index}-{brush_basename_raw}"

    temp_filepath = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{original_filename}")
    uploaded_file.save(temp_filepath)

    images, error_msg, temp_extract_dir = process_brushset(temp_filepath)
    os.remove(temp_filepath)

    if error_msg:
        if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)
        session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"message": error_msg}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    os.makedirs(session_dir, exist_ok=True)
    for i, img_path in enumerate(images):
        new_filename = f"{brush_basename}_{i + 1}.png"
        shutil.move(img_path, os.path.join(session_dir, new_filename))
        
    if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)

    # --- Step 3: If this is the LAST file, create the zip AND THEN deduct the credit. ---
    if is_last_file:
        final_zip_filename = f"converted_{secure_filename(session_id)}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        
        if not os.path.exists(session_dir) or not os.listdir(session_dir):
             shutil.rmtree(session_dir, ignore_errors=True)
             # UPDATED MESSAGE
             return jsonify({"message": "This .brushset doesn't contain any large stamp images."}), 400

        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in sorted(os.listdir(session_dir)):
                if item.endswith('.png'):
                    final_arcname = item.split('-', 1)[1] if '-' in item else item
                    zf.write(os.path.join(session_dir, item), final_arcname)
        
        shutil.rmtree(session_dir, ignore_errors=True)
        
        try:
            decrement_response = supabase.rpc('decrement_session', {'key_to_update': license_key}).execute()
            if decrement_response.data is not None:
                new_remaining_balance = decrement_response.data
            else:
                raise Exception("Failed to decrement session after successful conversion.")
        except Exception as e:
            print(f"CRITICAL: Conversion succeeded but credit deduction failed: {e}")
            balance_response = supabase.from_('licenses').select('sessions_remaining').eq('license_key', license_key).single().execute()
            new_remaining_balance = balance_response.data['sessions_remaining'] if balance_response.data else 0

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
