# --- Brush Processing Function (Corrected) ---
def process_brushset(filepath, make_transparent=True):
    # Create a unique temporary directory for this specific brushset processing
    temp_extract_dir = os.path.join(UPLOAD_FOLDER, f"extract_{uuid.uuid4()}")
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    extracted_image_paths = []
    try:
        # This is the line that was likely causing the error.
        # It should correctly open the zip file.
        with zipfile.ZipFile(filepath, 'r') as brushset_zip:
            # Limit the number of files to prevent zip bombs
            if len(brushset_zip.infolist()) > MAX_BRUSH_COUNT * 2:
                 return [], "Error: Brush set contains too many items."
            brushset_zip.extractall(temp_extract_dir)

        # Walk through the extracted files and subdirectories
        for root, dirs, files in os.walk(temp_extract_dir):
            for name in files:
                item_path = os.path.join(root, name)
                try:
                    with Image.open(item_path) as img:
                        width, height = img.size
                        if width >= MIN_IMAGE_DIMENSION and height >= MIN_IMAGE_DIMENSION:
                            final_image = img.copy()
                            if make_transparent and final_image.mode == 'L':
                                transparent_img = Image.new('RGBA', final_image.size, (0, 0, 0, 0))
                                transparent_img.putalpha(final_image)
                                final_image = transparent_img
                            
                            # Save the processed image to a temporary file with a unique name
                            temp_png_path = os.path.join(temp_extract_dir, f"processed_{uuid.uuid4()}.png")
                            final_image.save(temp_png_path, 'PNG')
                            extracted_image_paths.append(temp_png_path)
                except (IOError, SyntaxError):
                    # This catches files that are not images or are corrupted
                    continue
        
        if not extracted_image_paths:
            return [], "Error: No valid stamp images found in the file (min 1024x1024px)."
        
        # Return the paths and the parent directory for later cleanup
        return extracted_image_paths, temp_extract_dir, None

    except zipfile.BadZipFile:
        return [], None, "Error: The uploaded file is not a valid .brushset file."
    except Exception as e:
        print(f"Error during brushset processing: {e}")
        return [], None, "An unexpected error occurred during processing."
    # The 'finally' block is removed because we need to clean up the directory later
