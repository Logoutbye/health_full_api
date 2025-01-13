from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
from difflib import get_close_matches

import os
import pytesseract
from pdf2image import convert_from_path
from PyPDF2 import PdfReader
from PIL import Image, ImageFilter
import re
import cv2
import numpy as np

app = Flask(__name__)

def get_closest_unit(extracted_unit):
    """
    Find the closest matching unit from the predefined list using string similarity.
    """
    if extracted_unit in ACCEPTED_UNITS:
        return extracted_unit  # Exact match found
    closest_match = get_close_matches(extracted_unit, ACCEPTED_UNITS, n=1, cutoff=0.6)
    return closest_match[0] if closest_match else None  # Return closest match if found, else None

# Predefined list of accepted units for blood metrics
ACCEPTED_UNITS = [
    'g/dL', 'g/L', '10^6/μL', '10^3/μL', '/μL', 'fl', 'pg', '%', 'x10^9/L', 
    'x10^12/L', 'mm^3', 'μm^3', 'cells/mm^3', 'cells/μL'
]
# Define upload folder and allowed extensions
UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['APPLICATION_ROOT'] = '/bloodreports'

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Function to calculate sharpness using Laplacian variance
def is_image_quality_good(image_path):
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    laplacian_var = cv2.Laplacian(image, cv2.CV_64F).var()
    return laplacian_var > 100  # Threshold for sharpness

# Function to enhance image quality using OpenCV
def enhance_image(image_path):
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Slightly sharpen the image by increasing sharpness ~20%
    kernel = np.array([[0, -1, 0], [-0.8, 5, -1.2], [0, -1, 0]])
    
    
    sharpened = cv2.filter2D(gray, -1, kernel)
    return sharpened

# Extract text from an image using pytesseract
def extract_text_from_image(image_path):
    enhanced_image = enhance_image(image_path)
    temp_path = "enhanced_image.png"
    cv2.imwrite(temp_path, enhanced_image)

    if not is_image_quality_good(temp_path):
        return None, "Image quality is too low for accurate text extraction, even after enhancement."

    text = pytesseract.image_to_string(Image.open(temp_path))
    return text, None


# Extract text from a PDF file
def extract_text_from_pdf(pdf_path):
    text = ""
    # Convert PDF to images if necessary
    images = convert_from_path(pdf_path)
    for image in images:
        quality_good = is_image_quality_good(image.filename)
        if not quality_good:
            return None, "PDF page quality is too low for accurate text extraction."
        text += pytesseract.image_to_string(image)
    return text, None

# Function to check if file is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to clean up OCR-extracted text
def clean_extracted_text(text):
    text = re.sub(r'\s+', ' ', text)  # Replace multiple spaces with a single space
    text = re.sub(r'[|]', 'I', text)  # Fix common OCR misreads (e.g., "|" to "I")
    text = text.strip()
    return text


def preprocess_line(line, blood_metrics):
    shorthand_mappings = {
        "Mean Corpuscular Volume (MCV)": "MCV",
        "Hemoglobin (Hb)": "Haemoglobin",
        "Packed Cell Volume (PCV)": "PCV",
        "Mcv":"MCV",
        "Mch": "MCH",
        "Mchc": "MCHC",
        "Total WBC count":"WBC",
        "Total RBC count":"RBC",
    }

    lower_line = line.lower()

    # Check if the line contains any blood metric keywords
    if not any(keyword in lower_line for keywords in blood_metrics.values() for keyword in keywords):
        return None  # Skip this line if no keywords are found
   
    # Replace multi-word test names with single tokens
    for key, keywords in blood_metrics.items():
        for keyword in keywords:
            if keyword in lower_line:
                line = line.replace(keyword, key)  # Replace with the standard key name

    for long_name, shorthand in shorthand_mappings.items():
        line = line.replace(long_name, shorthand)
    
    # Handle ranges and remove unnecessary spaces
    line = line.replace(" - ", "-").replace(" -", "-").replace("- ", "-")
    line = re.sub(r'(\d+)[^\d\.\-\s]*', r'\1', line)  # Remove any non-numeric characters after numbers
    line = re.sub(r'(\d*)[eE](\d+)', r'\1\2', line)  # Remove 'e' from 'e000' (e.g., "e000" -> "000")

    formatted_line = []
    parts = line.split()
    for i, part in enumerate(parts):
        # Add comma after test name if followed by numeric or another significant part
        if i > 0 and parts[i - 1].isalpha() and (part[0].isdigit() or part[0] == "-"):
            formatted_line.append(',')

        # Add comma after numeric value or range if followed by unit
        if i > 0 and (parts[i - 1].replace(".", "", 1).isdigit() or "-" in parts[i - 1]):
            if i + 1 < len(parts) and not parts[i + 1][0].isdigit() and parts[i + 1][0] != "-":
                formatted_line.append(',')
            elif i + 1 == len(parts):  # If it's the last part
                formatted_line.append(',')

        # Add the current part
        formatted_line.append(part)

    # Join and clean up
    line = " ".join(formatted_line).replace(" ,", ",").replace(", ", ",").replace(" ,", ",")
    line = re.sub(r'\b(feumm|umm|/eumm)\b', '/cumm', line)

    return line

def parse_blood_metrics(text):
    blood_metrics = {
        "Total Leucocyte Count": ["total leucocyte count"],
        "HAEMOGLOBIN": ["hemoglobin", "hgb", "haemoglobin", "hemoglobin (hb)", "haemoglobin hb)"],
        "RBC": ["rbc count", "total rbc count"],
        "WBC": ["wbc", "white blood cells", "total wbc count"],
        "Platelet Count": ["platelet count", "plt"],
        "Neutrophils": ["neutrophils"],
        "Absolute Neutrophils": ["absolute neutrophils"],
        "Absolute Lymphocytes": ["absolute lymphocytes"],
        "Absolute Eosinophils": ["absolute eosinophils"],
        "Absolute Monocytes": ["absolute monocytes"],
        "Lymphocytes": ["lymphocytes"],
        "Monocytes": ["monocytes"],
        "Eosinophils": ["eosinophils"],
        "Basophils": ["basophils"],
        "MCH": ["mch"],
        "MCHC": ["mchc"],
        "MCV": ["mcv"],
    }

    def is_number(value):
        """Check if a value is a valid number (integer or float)."""
        try:
            float(value)  # Try converting to float
            return True
        except ValueError:
            return False
    def extract_range(token):
        """Extract a range if the token contains a valid range, including decimal values."""
        if "-" in token:
            # Handle ranges with decimals or integers
            parts = token.split("-")
            if len(parts) == 2:
                # Check if both parts are valid numbers (integer or float)
                if (is_number(parts[0]) and is_number(parts[1])):
                    return token  # Return the range as-is
        return None  # No valid range found


    # Split the input into processed lines
    processed_lines = []
    for line in text.splitlines():
        processed_line = preprocess_line(line, blood_metrics)

        # If the processed line is not empty and contains commas
        if processed_line and ',' in processed_line:
            # Extract all the numeric values (integers and decimals)
            parts = processed_line.split(',')
            
            # Clean parts to keep only the numeric values (remove non-numeric parts)
            cleaned_parts = []
            for part in parts:
                # If there is a hyphen in the part, handle it as a range
                if '-' in part:
                    # Preserve the hyphen and numbers in the range
                    part = re.sub(r'(\d+)-(\d+)', r'\1-\2', part)  # Keeps the range intact
                    cleaned_parts.append(part)
                else:
                    # Use regex to extract numbers (integers or decimals)
                    numbers = re.findall(r'\d+\.\d+|\d+', part.strip())  # Match integers or decimals
                    
                    if numbers:  # If there are any numbers in this part, keep them
                        cleaned_parts.append(''.join(numbers))  # Join numbers together if multiple
                    else:
                        cleaned_parts.append(part.strip())  # Keep the part as-is if no number found
            
            # Rebuild the line with cleaned parts
            cleaned_processed_line = ",".join(cleaned_parts)
            
            # Append to the processed lines list
            processed_lines.append(cleaned_processed_line)
            print("processed_line:", cleaned_processed_line)
    # Initialize result list
    result = []

    # Process each relevant line
    for line in processed_lines:
        tokens = line.split(",")
        tokens = [token.strip() for token in tokens if token.strip()]  # Clean up tokens

        if not tokens:
            continue

        # Extract the test name from the first token and capitalize it
        test_name = tokens[0]  # Ensure full capitalization

        # Initialize variables
        value = None
        ref_range = None
        unit = None

        # Process remaining tokens
        for token in tokens[1:]:
            if value is None and token.replace(".", "", 1).isdigit():  # Check for numeric value
                value = token
            elif ref_range is None:
                range_candidate = extract_range(token)
                if range_candidate:
                    ref_range = range_candidate
            elif unit is None:
                unit = unit = get_closest_unit(token)

        # Append parsed data with consistently formatted test name
        result.append({
            "Test": test_name,
            "Value": value,
            # "Range": ref_range,
            "Unit": unit,
        })

    return result

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist('file')  # Get list of files

    if not files:
        return jsonify({"error": "No files selected"}), 400

    combined_text = ""
    error = None

    # Process each file
    for file in files:
        if file.filename == '':
            return jsonify({"error": "One or more files have no selected filename"}), 400

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Extract text based on file type
            if filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}:
                extracted_text, error = extract_text_from_image(file_path)
            elif filename.rsplit('.', 1)[1].lower() == 'pdf':
                extracted_text, error = extract_text_from_pdf(file_path)
            else:
                return jsonify({"error": "Unsupported file type"}), 400

            if error:
                return jsonify({"error": error}), 400

            # Append the text from this file to the combined text
            combined_text += extracted_text + "\n"  # Adding a newline for separation between files

        else:
            return jsonify({"error": f"File {file.filename} has an invalid type"}), 400

    # Parse blood metrics from the combined text
    parsed_data = parse_blood_metrics(combined_text)
    return jsonify({"parsed_data": parsed_data}), 200


@app.route('/', methods=['GET'])
def get_instructions():
    """
    GET endpoint to provide beautified HTML instructions on how to use the upload API.
    """
    return render_template('instructions.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0')
