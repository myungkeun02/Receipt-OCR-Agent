import os
import logging
from http import HTTPStatus
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import re
from datetime import datetime
from flask_restx import Api, Resource, fields, reqparse
from werkzeug.datastructures import FileStorage
import requests
import uuid
import base64
import json
import time
from openai import OpenAI

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
api = Api(app, version='1.0', title='Korean Receipt Processor API',
          description='API for extracting data and categorizing expenses from Korean receipts.')

# --- Configuration ---
CLOVA_OCR_API_KEY = os.getenv("CLOVA_OCR_API_KEY")
CLOVA_OCR_ENDPOINT = os.getenv("CLOVA_OCR_ENDPOINT")
LLM_API_KEY = os.getenv("LLM_API_KEY")

# --- Clean and Sanity Check Environment Variables ---
if CLOVA_OCR_ENDPOINT:
    CLOVA_OCR_ENDPOINT = "".join(CLOVA_OCR_ENDPOINT.split()) # Remove all whitespace, including newlines
    if CLOVA_OCR_ENDPOINT.startswith("http://"):
        logging.warning(f"Incorrect 'http' scheme detected in endpoint. Correcting to 'https'. Original: {CLOVA_OCR_ENDPOINT}")
        CLOVA_OCR_ENDPOINT = CLOVA_OCR_ENDPOINT.replace("http://", "https://", 1)

logging.info(f"Loaded and cleaned CLOVA_OCR_ENDPOINT: {CLOVA_OCR_ENDPOINT}")
logging.info(f"Loaded LLM_API_KEY: {'Set' if LLM_API_KEY else 'Not Set'}")


# --- OCR and LLM Functions ---

def call_clova_ocr(image_data):
    """
    Calls the Naver CLOVA OCR API to extract text from a receipt image.
    """
    if not CLOVA_OCR_API_KEY or not CLOVA_OCR_ENDPOINT:
        logging.warning("CLOVA OCR API key or endpoint not set. Using mock data.")
        # This mock data must include the 'images' and 'fields' structure
        return {
            "images": [{
                "fields": [
                    {"inferText": "Mock Merchant"},
                    {"inferText": "2025-07-11"},
                    {"inferText": "12,345원"}
                ]
            }]
        }

    try:
        payload = {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [
                {
                    "format": "jpeg",
                    "name": "receipt",
                    "data": base64.b64encode(image_data).decode('utf-8')
                }
            ]
        }

        headers = {
            "X-OCR-SECRET": CLOVA_OCR_API_KEY,
            "Content-Type": "application/json"
        }

        logging.info(f"Sending request to CLOVA OCR endpoint: {CLOVA_OCR_ENDPOINT}")
        response = requests.post(CLOVA_OCR_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        ocr_result = response.json()
        logging.info("Received successful response from CLOVA OCR.")
        
        # Return the entire raw result for the next processing step
        return ocr_result

    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling CLOVA OCR API: {e}", exc_info=True)
        raise
    except (KeyError, IndexError, TypeError) as e:
        logging.error(f"Error processing OCR response after call: {e}", exc_info=True)
        raise ValueError("Failed to process data from OCR response.")


def call_llm_for_extraction_and_categorization(raw_text):
    """
    Calls OpenAI API to extract structured data and categorize the expense from raw OCR text.
    """
    if not LLM_API_KEY:
        logging.warning("LLM API key not set. Using mock extraction.")
        return {
            "total_price": 12345,
            "transaction_date": "2025-07-11",
            "approval_no": "987654",
            "merchant_name": "Mock Merchant",
            "expense_category": "Mock Category"
        }

    try:
        client = OpenAI(api_key=LLM_API_KEY)

        prompt = (f"""You are an expert data extraction API. Analyze the raw text from a Korean receipt below. 
Extract the merchant name, transaction date, total price, and approval number. 
Also, determine the expense category from this list: ['Groceries', 'Dining', 'Transportation', 'Shopping', 'Health & Beauty', 'Entertainment', 'Utilities', 'Development Tools / Subscription', 'Travel', 'Education', 'Other', 'Uncategorized'].
Respond ONLY with a valid JSON object with the following keys: "merchant_name", "transaction_date", "total_price", "approval_no", "expense_category".
- "transaction_date" must be in YYYY-MM-DD format.
- "total_price" must be an integer.
- If a field cannot be found, its value should be null.

--- RAW RECEIPT TEXT ---
{raw_text}
--- END RAW RECEIPT TEXT ---""")

        logging.info("Sending request to OpenAI for data extraction.")
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        llm_result_str = response.choices[0].message.content
        
        logging.info(f"Received raw response from LLM: {llm_result_str}")
        
        if not llm_result_str:
            raise ValueError("LLM returned an empty response.")
            
        extracted_data = json.loads(llm_result_str)
        return extracted_data

    except Exception as e:
        logging.error(f"Error calling or parsing OpenAI API response: {e}", exc_info=True)
        return {"error": "OpenAI API request failed or returned invalid data."}


# --- Helper Functions for Data Parsing ---

def parse_total_price(price_str):
    """
    Parses a price string (e.g., "69445원", "₩69,445") into an integer.
    """
    # Remove non-numeric characters except for the decimal point
    numeric_str = re.sub(r'[^\d.]', '', price_str)
    try:
        # Convert to float first to handle potential decimal values, then to int if whole number
        return int(float(numeric_str))
    except ValueError:
        return None

def parse_transaction_date(date_str):
    """
    Parses various Korean date formats into "YYYY-MM-DD".
    Handles "YY.MM.DD", "YYYY.MM.DD", "YYYY년 MM월 DD일".
    """
    date_str = date_str.strip()
    
    # Try YYYY년 MM월 DD일 format
    match = re.match(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', date_str)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    # Try YY.MM.DD or YYYY.MM.DD format
    match = re.match(r'(\d{2}|\d{4})\.(\d{1,2})\.(\d{1,2})', date_str)
    if match:
        year, month, day = match.groups()
        if len(year) == 2:
            year = f"20{year}" # Assume 21st century for 2-digit years
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    
    # If no specific format matches, try a general date parser (less robust)
    try:
        # Attempt to parse with common formats
        for fmt in ["%Y-%m-%d", "%y-%m-%d", "%Y.%m.%d", "%y.%m.%d", "%Y/%m/%d", "%y/%m/%d"]:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
    except ValueError:
        pass

    return None # Return None if parsing fails


# --- API Documentation Models and Parsers ---

receipt_parser = reqparse.RequestParser()
receipt_parser.add_argument('image', type=FileStorage, location='files', required=True, help='Image file of the Korean receipt (JPEG, PNG)')

receipt_output_model = api.model('ReceiptOutput', {
    'total_price': fields.Integer(required=False, description='The total price extracted by the LLM.'),
    'transaction_date': fields.String(required=False, description='The transaction date extracted by the LLM (YYYY-MM-DD).'),
    'approval_no': fields.String(required=False, description='The approval number extracted by the LLM.'),
    'merchant_name': fields.String(required=False, description='The merchant name extracted by the LLM.'),
    'expense_category': fields.String(required=False, description='The expense category inferred by the LLM.'),
})

# --- API Endpoint ---

ns = api.namespace('receipt', description='Receipt processing operations')

@ns.route('/process-receipt')
class ProcessReceipt(Resource):
    @ns.doc('process_korean_receipt')
    @ns.expect(receipt_parser)
    @ns.marshal_with(receipt_output_model, code=200, description='Receipt processed successfully')  # type: ignore
    @ns.response(HTTPStatus.BAD_REQUEST, 'Invalid input or failed data extraction')
    @ns.response(HTTPStatus.INTERNAL_SERVER_ERROR, 'Internal server error during OCR or LLM processing')
    def post(self):
        """
        Processes a Korean receipt image to extract key data and categorize the expense.
        """
        logging.info("Received request to process receipt.")
        args = receipt_parser.parse_args()
        image_file = args['image']

        if not image_file:
            logging.error("Aborting: No image file provided.")
            ns.abort(HTTPStatus.BAD_REQUEST, "No image file provided or invalid file.")
        
        logging.info(f"Processing image: {image_file.filename}")
        # Read image data
        image_data = image_file.read()

        # Step 1: OCR Data Extraction
        try:
            logging.info("Calling CLOVA OCR service.")
            ocr_raw_data = call_clova_ocr(image_data)
            # No longer logging the full JSON here to avoid clutter, as the next step logs the crucial part.
        except Exception as e:
            logging.error(f"OCR processing failed: {e}", exc_info=True)
            ns.abort(HTTPStatus.INTERNAL_SERVER_ERROR, f"OCR processing failed: {str(e)}")

        # Step 2: Assemble all OCR text into a single block
        all_text_fields = ocr_raw_data.get('images', [{}])[0].get('fields', [])
        raw_text_block = "\\n".join([field.get('inferText', '') for field in all_text_fields])

        # As requested, log the entire text block first
        logging.info(f"--- Raw OCR Text Block ---\\n{raw_text_block}")

        if not raw_text_block.strip():
            logging.error("Aborting: OCR did not return any text.")
            ns.abort(HTTPStatus.BAD_REQUEST, "Failed to extract any text from the receipt image.")

        # Step 3: Use LLM to extract and categorize from the raw text block
        extracted_data = call_llm_for_extraction_and_categorization(raw_text_block)

        if "error" in extracted_data:
            ns.abort(HTTPStatus.INTERNAL_SERVER_ERROR, extracted_data["error"])

        # Step 4: Final JSON Output from LLM's structured response
        response_data = {
            "total_price": extracted_data.get("total_price"),
            "transaction_date": extracted_data.get("transaction_date"),
            "approval_no": extracted_data.get("approval_no"),
            "merchant_name": extracted_data.get("merchant_name"),
            "expense_category": extracted_data.get("expense_category")
        }
        
        logging.info(f"Successfully processed receipt. Sending response: {response_data}")

        return response_data, 200

if __name__ == '__main__':
    # For development, run with debug=True
    # In production, use a production-ready WSGI server like Gunicorn
    app.run(debug=True, host='0.0.0.0', port=5001)