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
# IMPORTANT: Make sure you have set this environment variable in your Render hosting environment.
LEMONSQUEEZY_API_KEY = os.environ.get('LEMONSQUEEZY_API_KEY')
LEMONSQUEEZY_API_URL = "https://api.lemonsqueezy.com/v1"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy Helper Functions (REVISED) ---
def validate_license_key(license_key):
    """
    Validates a license key with the Lemon Squeezy API using best practices.
    """
    if not LEMONSQUEEZY_API_KEY:
        print("CRITICAL_ERROR: LEMONSQUEEZY_API_KEY environment variable is not set.")
        return None, "Server configuration error: API Key is missing."

    headers = {
        'Accept': 'application/vnd.api+json',
        'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'
    }
    
    # Use the 'params' argument to let the 'requests' library handle URL encoding.
    # This is the key fix for the validation issue.
    params = {'filter[key]': license_key}
    
    try:
        response = requests.get(
            f"{LEMONSQUEEZY_API_URL}/license-keys",
            headers=headers,
            params=params,
            timeout=15
        )
        response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        # Check if the 'data' array is present and contains at least one key
        if data.get('data') and len(data['data']) > 0:
            return data['data'][0], None # Return the first matching key data
        
        return None, "The provided license key is not valid or could not be found."
        
    except requests.exceptions.HTTPError as e:
        error_message = f"API Error: Received status code {e.response.status_code}."
        try:
            # Try to parse the specific error detail from Lemon Squeezy's response
            error_details = e.response.json()
            error_message = error_details.get('errors', [{}])[0].get('detail', 'An unknown API error occurred.')
        except (ValueError, IndexError, KeyError):
            # Fallback if the error response is not in the expected format
            print(f"Could not parse Lemon Squeezy error response: {e.response.text}")
        
        print(f"HTTP Error from Lemon Squeezy: {error_message}")
        return None, f"API Error: {error_message}"
        
    except requests.exceptions.RequestException as e:
        # Handles network errors (DNS failure, refused connection, etc.)
        print(f"API Request Error: {e}")
        return None, "Could not connect to the license server. Please check the server's network connection."

def increment_license_usage(key_id):
    """
    Increments the usage count for a given license key ID.
    """
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
                'increment': 1
            }
        }
    }
    try:
        response = requests.patch(f"{LEMONSQUEEZY_API_URL}/license-keys/{key_id}", headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API Error during usage increment for key ID {key_id}: {e}")
        # This is a server-side issue, so we don't return an error to the user here,
        # as they have already received their file. We just log it.
        return None

# --- Brushset Processing Function ---
def process_brushset(filepath):
    base_filename = os.path.basename(filepath)
    # Use a unique ID for the extraction folder to prevent conflicts
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4().hex}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    image_paths = []
    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            brushset_zip.extractall(temp_extract_dir)
            for root, _, files in os.walk(temp_extract_dir):
                for name in files:
                    # Ensure the file is likely an image before trying to open it
                    if name.lower().endswith(('.png', '.jpg', '.jpeg')):
                        try:
                            img_path = os.path.join(root, name)
                            with Image.open(img_path) as img:
                                # This logic correctly identifies the brush shape images
                                if img.width >= 1024 and img.height >= 1024 and name.lower() != 'artwork.png':
                                    image_paths.append(img_path)
                        except (IOError, SyntaxError):
                            # This file might be a corrupted image or not an image at all, skip it
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

    # --- On the first file of a session, validate the license key ---
    if is_first_file:
        if not license_key:
            return jsonify({"message": "License Key is required."}), 400
            
        key_data, error_message = validate_license_key(license_key)
        if error_message:
            return jsonify({"message": error_message}), 400
        
        key_attributes = key_data.get('attributes', {})
        if key_attributes.get('status') != 'active':
            return jsonify({"message": f"This license key is {key_attributes.get('status', 'not active')}."}), 403
        
        activation_limit = key_attributes.get('activation_limit')
        uses = key_attributes.get('uses', 0)

        # Check if the key has uses left
        if activation_limit is not None and uses >= activation_limit:
            return jsonify({"message": "This license key has reached its usage limit."}), 403

        # Create a secure session directory to store files and the key ID
        session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
        os.makedirs(session_dir, exist_ok=True)
        
        # Store the key ID to increment its usage later
        with open(os.path.join(session_dir, 'key_id.txt'), 'w') as f:
            f.write(str(key_data['id']))

    # --- Process the uploaded file ---
    uploaded_file = request.files.get('brush_file')
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"message": "No file was provided in the request."}), 400

    # Secure the filename and create a temporary path
    filename = secure_filename(uploaded_file.filename)
    temp_filepath = os.path.join(UPLOAD_FOLDER, f"temp_{uuid.uuid4().hex}_{filename}")
    uploaded_file.save(temp_filepath)

    images, error_msg, temp_extract_dir = process_brushset(temp_filepath)
    os.remove(temp_filepath) # Clean up the temporary uploaded file immediately

    if error_msg:
        if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return jsonify({"message": error_msg}), 400

    # Move the extracted images to the session directory
    session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    for img_path in images:
        # Move file to the session folder, giving it a unique name to avoid collisions
        shutil.move(img_path, os.path.join(session_dir, f"{uuid.uuid4().hex}.png"))
    if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)

    # --- On the last file, zip everything up and send the download link ---
    if is_last_file:
        final_zip_filename = f"converted_{secure_filename(session_id)}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        
        # Create the final zip file
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in os.listdir(session_dir):
                if item.endswith('.png'): # Only zip the extracted images
                    zf.write(os.path.join(session_dir, item), os.path.basename(item))
        
        # Read the key ID and increment its usage
        key_id_path = os.path.join(session_dir, 'key_id.txt')
        if os.path.exists(key_id_path):
            with open(key_id_path, 'r') as f:
                key_id_to_increment = f.read().strip()
            increment_license_usage(key_id_to_increment)

        # Clean up the session directory
        shutil.rmtree(session_dir, ignore_errors=True)

        return jsonify({
            "message": "Processing complete. Your download is ready.",
            "download_url": f"/download-zip/{final_zip_filename}"
        })

    # If not the last file, just return a success message
    return jsonify({"message": "File processed successfully. Awaiting next file."})

@app.route('/download-zip/<filename>')
def download_zip(filename):
    # Sanitize filename one last time before serving
    safe_filename = secure_filename(filename)
    directory = UPLOAD_FOLDER
    
    try:
        # Send the file for download
        return send_from_directory(directory, safe_filename, as_attachment=True, download_name="Procreate_Stamps.zip")
    finally:
        # Clean up the zip file from the server after sending it
        try:
            os.remove(os.path.join(directory, safe_filename))
        except OSError as e:
            print(f"Error cleaning up zip file '{safe_filename}': {e}")

if __name__ == '__main__':
    # Use debug=False in a production environment
    app.run(debug=True, port=5001)
