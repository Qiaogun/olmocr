from flask import Flask, request, jsonify, send_from_directory
from pypdf import PdfReader
import io
import os

app = Flask(__name__, static_folder=os.path.dirname(__file__), static_url_path="")

@app.route('/')
def root():
    return app.send_static_file('index.html')

@app.route('/api/ocr', methods=['POST'])
def api_ocr():
    file = request.files.get('pdf')
    if not file:
        return jsonify({'error': 'no file uploaded'}), 400
    try:
        reader = PdfReader(file.stream)
        pages = [page.extract_text() or '' for page in reader.pages]
        return jsonify({'pages': pages})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

