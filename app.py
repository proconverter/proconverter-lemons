import os
import requests
import zipfile
import shutil
import time
from flask import Flask, render_template, request, jsonify, send_from_directory, after_this_request
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)

# --- Configuration ---
ETSY_API_KEY = os.environ.get('ETSY_API_KEY')
ETSY_SHOP_ID = "PresentAndCherish"
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def process_brushset(filepath, make_transparent=False):
    base_filename = os.path.basename(filepath)
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{base_filename}")
    temp_output_dir = os.path.join(UPLOAD_FOLDER, f"output_{base_filename}")
    
    if os.path.exists(temp_extract_dir): shutil.rmtree(temp_extract_dir)
    if os.path.exists(temp_output_dir): shutil.rmtree(temp_output_dir)
    os.makedirs(temp_extract_dir, exist_ok=True)
    os.makedirs(temp_output_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            brushset_zip.extractall(temp_extract_dir)

        extracted_images = []
        for root, dirs, files in os.walk(temp_extract_dir):
            for name in files:
                img_path = os.path.join(root, name)
                try:
                    with Image.open(img_path) as img:
                        width, height = img.size
                        if width >= 1024 and height >= 1024:
                            extracted_images.append(img_path)
                except (IOError, SyntaxError):
                    continue
        
        if not extracted_images:
            return None, "Error: No brushes larger than 1024x1024 were found."

        for i, img_path in enumerate(extracted_images):
            with Image.open(img_path) as img:
                final_image = img
                if make_transparent and img.mode == 'L':
                    transparent_img = Image.new('RGBA', img.size, (0, 0, 0, 0))
                    transparent_img.putalpha(img)
                    final_image = transparent_img
                
                output_filename = f"brush_{i + 1}.png"
                output_image_path = os.path.join(temp_output_dir, output_filename)
                final_image.save(output_image_path, 'PNG')

        return temp_output_dir, None
    finally:
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/convert-single', methods=['POST'])
def convert_single():
    order_id = request.form.get('order_id')
    make_transparent = request.form.get('make_transparent') == 'true'
    file_index = int(request.form.get('file_index', 0))
    total_files = int(request.form.get('total_files', 1))
    uploaded_file = request.files.get('brush_file')

    if not all([order_id, uploaded_file]):
        return jsonify({"message": "Missing required data."}), 400

    session_id = secure_filename(order_id)
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)

    if file_index == 0:
        # Clean up old session data if a new conversion is started with the same ID
        for item in os.listdir(session_folder):
            item_path = os.path.join(session_folder, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        try:
            # --- Live Etsy API validation block ---
            # api_url = f"https://openapi.etsy.com/v3/application/shops/{ETSY_SHOP_ID}/receipts/{order_id.strip( )}"
            # headers = {'x-api-key': ETSY_API_KEY}
            # response = requests.get(api_url, headers=headers, timeout=10)
            # if response.status_code != 200:
            #     return jsonify({"message": "Could not verify Etsy Order ID."}), 402
            pass
        except requests.exceptions.RequestException:
            return jsonify({"message": "Could not connect to Etsy servers."}), 500

    filename = secure_filename(uploaded_file.filename)
    filepath = os.path.join(session_folder, filename)
    uploaded_file.save(filepath)

    output_dir, error = process_brushset(filepath, make_transparent)
    os.remove(filepath)

    if error:
        return jsonify({"message": error}), 400
    
    staging_folder = os.path.join(session_folder, 'staging', filename.replace('.brushset', ''))
    os.makedirs(staging_folder, exist_ok=True)
    for png_file in os.listdir(output_dir):
        shutil.move(os.path.join(output_dir, png_file), staging_folder)
    shutil.rmtree(output_dir)

    if file_index == total_files - 1:
        final_zip_filename = f"Converted_Brushes_{order_id}.zip"
        final_zip_path = os.path.join(session_folder, final_zip_filename)
        
        with zipfile.ZipFile(final_zip_path, 'w') as final_zip:
            final_staging_area = os.path.join(session_folder, 'staging')
            for root, dirs, files in os.walk(final_staging_area):
                for file in files:
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, final_staging_area)
                    final_zip.write(full_path, arcname)
        
        shutil.rmtree(os.path.join(session_folder, 'staging'))
        
        return jsonify({
            "message": "All files processed.",
            "download_url": f"/download-zip/{session_id}/{final_zip_filename}"
        })

    return jsonify({"message": "File processed successfully."})

@app.route('/download-zip/<session_id>/<filename>')
def download_zip(session_id, filename):
    directory = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    safe_filename = secure_filename(filename)

    @after_this_request
    def cleanup(response):
        # Clean up the entire session folder after the download is complete
        try:
            shutil.rmtree(directory)
            print(f"Cleaned up session folder: {directory}")
        except Exception as e:
            print(f"Error cleaning up session folder {directory}: {e}")
        return response

    return send_from_directory(directory, safe_filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
