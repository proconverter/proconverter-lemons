import os
import requests
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from PIL import Image
from supabase import create_client, Client
from postgrest.exceptions import APIError # Import APIError

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
        return None, "This file seems to be corrupted or isn't a valid .brushset.", None
    except Exception as e:
        print(f"Error processing brushset: {e}")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return None, "Something went wrong on our end. Please try again in a moment.", None

# --- Main Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

# --- CORRECTED Route to check license balance ---
@app.route('/check-license')
def check_license():
    license_key = request.args.get('license_key')
    if not license_key:
        return jsonify({"message": "License key is required."}), 400
    try:
        # The execute() call is now outside the main try block for specific error handling
        response = supabase.from_('licenses').select('sessions_remaining, is_active').eq('license_key', license_key).single().execute()
        
        # The .single() method ensures that if no row is found, it raises an error
        # that we can catch, but for robustness, we also check the data.
        if response.data is None:
            return jsonify({"message": "That license key wasn't found. Please check for typos."}), 404

        key_data = response.data
        if not key_data.get('is_active'):
            return jsonify({"message": "Your license isn't active yet. Please check your email."}), 403

        return jsonify({"remaining": key_data.get('sessions_remaining', 0)})

    except APIError as e:
        # This specifically catches the "No rows found" error from .single()
        if "No rows found" in e.message:
             return jsonify({"message": "That license key wasn't found. Please check for typos."}), 404
        # For
