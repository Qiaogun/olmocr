from flask import Flask, request, jsonify
from pypdf import PdfReader
import io
import os
import asyncio

app = Flask(__name__, static_folder=os.path.dirname(__file__), static_url_path="")

@app.route('/')
def root():
    return app.send_static_file('index.html')

def _extract_pages(data: bytes):
    reader = PdfReader(io.BytesIO(data))
    return [page.extract_text() or '' for page in reader.pages]


@app.route('/api/ocr', methods=['POST'])
async def api_ocr():
    file = request.files.get('pdf')
    if not file:
        return jsonify({'error': 'no file uploaded'}), 400
    try:
        data = await asyncio.to_thread(file.read)
        pages = await asyncio.to_thread(_extract_pages, data)
        return jsonify({'pages': pages})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

