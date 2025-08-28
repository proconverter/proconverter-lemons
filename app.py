# --- Main Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

# !!! REPLACE THE OLD /convert ROUTE WITH THIS NEW ONE !!!
@app.route('/convert', methods=['POST'])
def convert():
    # --- 1. Get all data from the request ---
    license_key = request.form.get('license_key')
    session_id = request.form.get('session_id')
    is_first_file = request.form.get('is_first_file') == 'true'
    is_last_file = request.form.get('is_last_file') == 'true'
    uploaded_file = request.files.get('brush_file')
    
    new_remaining_balance = -1 # Use -1 as a sentinel value

    # --- 2. Validate license and decrement session ONCE ---
    if is_first_file:
        try:
            # First, check if the key is valid and has sessions
            validation_response = supabase.from_('licenses').select('sessions_remaining, is_active').eq('license_key', license_key).single().execute()
            if validation_response.data is None:
                return jsonify({"message": "This license key does not exist."}), 404
            
            key_data = validation_response.data
            if not key_data.get('is_active'):
                return jsonify({"message": "This license key has not been activated yet."}), 403
            if key_data.get('sessions_remaining', 0) <= 0:
                return jsonify({"message": "This license key has reached its usage limit."}), 403

            # If valid, THEN decrement the session count
            decrement_response = supabase.rpc('decrement_session', {'key_to_update': license_key}).execute()
            if decrement_response.error:
                raise Exception(decrement_response.error.message)
            
            # The function returns the new balance, so we store it
            new_remaining_balance = decrement_response.data

        except Exception as e:
            print(f"Supabase validation/decrement error: {e}")
            return jsonify({"message": f"Could not validate or update license: {e}"}), 500

    # --- 3. Process the uploaded file ---
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
        return jsonify({"message": error_msg}), 400

    session_dir = os.path.join(UPLOAD_FOLDER, secure_filename(session_id))
    os.makedirs(session_dir, exist_ok=True)
    for i, img_path in enumerate(images):
        new_filename = f"{brush_basename}_{i + 1}.png"
        shutil.move(img_path, os.path.join(session_dir, new_filename))
        
    if temp_extract_dir: shutil.rmtree(temp_extract_dir, ignore_errors=True)

    # --- 4. If it's the last file, create the zip and return the final balance ---
    if is_last_file:
        final_zip_filename = f"converted_{secure_filename(session_id)}.zip"
        final_zip_path = os.path.join(UPLOAD_FOLDER, final_zip_filename)
        with zipfile.ZipFile(final_zip_path, 'w') as zf:
            for item in sorted(os.listdir(session_dir)):
                if item.endswith('.png'):
                    final_arcname = item.split('-', 1)[1] if '-' in item else item
                    zf.write(os.path.join(session_dir, item), final_arcname)
        
        shutil.rmtree(session_dir, ignore_errors=True)
        
        # If the balance wasn't set on the first file (e.g., a single-file upload),
        # we need to fetch it now. This is a fallback.
        if new_remaining_balance == -1:
             try:
                final_balance_response = supabase.from_('licenses').select('sessions_remaining').eq('license_key', license_key).single().execute()
                if final_balance_response.data:
                    new_remaining_balance = final_balance_response.data['sessions_remaining']
             except Exception:
                # If it fails, we still proceed but can't show the new balance.
                pass

        return jsonify({
            "message": "Processing complete.",
            "download_url": f"/download-zip/{final_zip_filename}",
            "remaining": new_remaining_balance 
        })

    # --- 5. If not the last file, just return a success message ---
    return jsonify({"message": "File processed successfully."})

# The rest of your app.py file (/download-zip route, etc.) remains the same.
