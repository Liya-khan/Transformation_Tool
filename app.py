import os
import shutil
import tempfile
import zipfile
from flask import Flask, request, jsonify, send_file, render_template
import geopandas as gpd
from pyproj import CRS
from werkzeug.utils import secure_filename


# The two core functions
def check_shapefile_completeness(zip_path):
    # ... (your existing code for this function)
    if not zip_path.lower().endswith(".zip"):
        raise ValueError("Input must be a zipped shapefile")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("The provided file is not a valid zip archive.")

    temp_dir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        shp_files = [f for f in os.listdir(temp_dir) if f.lower().endswith(".shp")]
        if not shp_files:
            raise FileNotFoundError("No .shp file found in the zipped folder.")

        shp_path = os.path.join(temp_dir, shp_files[0])

        base_name = os.path.splitext(shp_path)[0]
        required_exts = [".shp", ".shx", ".dbf", ".prj"]
        missing = [ext for ext in required_exts if not os.path.exists(base_name + ext)]
        if missing:
            raise FileNotFoundError(f"Incomplete shapefile. Missing: {', '.join(missing)}")

        return shp_path, temp_dir
    except Exception as e:
        shutil.rmtree(temp_dir)
        raise e


def reproject_shapefile(zip_path, target_crs):
    # ... (your existing code for this function)
    shp_path, temp_dir = None, None
    try:
        shp_path, temp_dir = check_shapefile_completeness(zip_path)

        gdf = gpd.read_file(shp_path)
        current_crs = gdf.crs
        if not current_crs:
            raise ValueError("No CRS information found in shapefile.")

        gdf = gdf.to_crs(target_crs)

        out_dir = os.path.join(temp_dir, "reprojected")
        os.makedirs(out_dir, exist_ok=True)
        out_shp = os.path.join(out_dir, os.path.basename(shp_path))
        gdf.to_file(out_shp)

        # Create the new zip file in a different temporary directory
        temp_output_dir = tempfile.mkdtemp()
        output_zip_path = os.path.join(temp_output_dir, f"reprojected_{target_crs.replace(':', '')}.zip")

        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in os.listdir(out_dir):
                full_path = os.path.join(out_dir, file)
                if os.path.isfile(full_path):
                    zf.write(full_path, os.path.basename(full_path))

        return output_zip_path, temp_dir, temp_output_dir

    except Exception:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        raise


# Flask application setup
app = Flask(__name__)

temp_file_store = {}


# --- NEW WEB APPLICATION ROUTES ---
@app.route('/')
def index():
    """Serves the main web page with the upload form."""
    return render_template('index.html')


# --- API ENDPOINTS ---
@app.route('/reproject_shapefile', methods=['POST'])
def reproject_api():
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith(".zip"):
        return jsonify({"error": "No selected file or invalid file type. Please upload a .zip file."}), 400

    target_crs = request.form.get('target_crs')
    if not target_crs:
        return jsonify({"error": "Missing 'target_crs' parameter"}), 400

    try:
        CRS(target_crs)
    except Exception:
        return jsonify({"error": f"Invalid target CRS: '{target_crs}'. Example: EPSG:4326"}), 400

    temp_upload_dir = tempfile.mkdtemp()
    uploaded_file_path = os.path.join(temp_upload_dir, secure_filename(file.filename))
    file.save(uploaded_file_path)

    try:
        output_zip_path, temp_input_dir, temp_output_dir = reproject_shapefile(uploaded_file_path, target_crs)

        file_id = os.path.basename(output_zip_path)
        temp_file_store[file_id] = {
            'path': output_zip_path,
            'temp_dirs': [temp_upload_dir, temp_input_dir, temp_output_dir]
        }

        return jsonify({
            "message": "Shapefile re-projected successfully",
            "download_link": f"{request.host_url}download_file/{file_id}"
        }), 200

    except (ValueError, FileNotFoundError) as e:
        shutil.rmtree(temp_upload_dir)
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        shutil.rmtree(temp_upload_dir)
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500


@app.route('/download_file/<string:file_id>')
def download_file(file_id):
    file_data = temp_file_store.get(file_id)
    if not file_data or not os.path.exists(file_data['path']):
        return jsonify({"error": "File not found or has expired"}), 404

    file_path = file_data['path']
    response = send_file(file_path, as_attachment=True)

    @response.call_on_close
    def cleanup_files():
        try:
            for d in file_data['temp_dirs']:
                shutil.rmtree(d)
        except Exception as e:
            print(f"Error during cleanup: {e}")
        del temp_file_store[file_id]

    return response


if __name__ == '__main__':
    app.run(debug=True)