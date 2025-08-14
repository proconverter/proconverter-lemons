import os
import requests
import zipfile
import shutil
import uuid
from flask import Flask, render_template, request, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)

# --- Configuration ---
# Get the API Key from the secure environment variable on Render
LEMONSQUEEZY_API_KEY = os.environ.get('LEMONSQUEEZY_API_KEY')
LEMONSQUEEZY_API_URL = "https://api.lemonsqueezy.com/v1"

# Your product's settings
# In the future, you could get this from the API, but for now, we hardcode it for simplicity.
# This corresponds to the "10 units" we set up in the product.
CREDITS_PER_PURCHASE = 10 

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_BRUSH_COUNT = 100
MIN_IMAGE_DIMENSION = 1024
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True )

# --- Lemon Squeezy API Helper Functions ---

def validate_license_key(license_key):
    """
    Checks a license key with the Lemon Squeezy API.
    Returns the license key object if valid and has uses left, otherwise None.
    """
    if not LEMONSQUEEZY_API_KEY:
        # This error is for you, the developer, if you forget to set the API key on Render
        raise ValueError("LEMONSQUEEZY_API_KEY is not set in environment variables.")

    headers = {
        'Accept': 'application/vnd.api+json',
        'Content-Type': 'application/vnd.api+json',
        'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'
    }
    params = {'license_key': license_key}
    
    try:
        response = requests.get(f"{LEMONSQUEEZY_API_URL}/licenses/validate", headers=headers, params=params)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        data = response.json()

        if data.get('valid'):
            # Key is valid, now check usage
            uses = data.get('meta', {}).get('uses', 0)
            if uses < CREDITS_PER_PURCHASE:
                return data # Return the whole object so we can use its ID later
            else:
                # Key is valid but has no uses left
                return None
        else:
            # Key is invalid
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Lemon Squeezy API: {e}")
        # In a real-world scenario, you might want to handle this more gracefully
        # For now, we'll treat it as an invalid key.
        return None

def increment_license_usage(license_id):
    """
    Increments the usage count of a given license key ID.
    """
    headers = {
        'Accept': 'application/vnd.api+json',
        'Content-Type': 'application/vnd.api+json',
        'Authorization': f'Bearer {LEMONSQUEEZY_API_KEY}'
    }
    payload = {
        "meta": {
            "uses": 1 # This is an increment, not a total
        }
    }
    try:
        response = requests.post(f"{LEMONSQUEEZY_API_URL}/licenses/{license_id}/increment", headers=headers, json=payload)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error incrementing license usage: {e}")
        return False

# --- Brush Processing Function (No changes here) ---
def process_brushset(filepath, make_transparent=True):
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4()}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    extracted_image_paths = []
    try:
        with zipfile.ZipFile
