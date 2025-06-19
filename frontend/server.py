from flask import Flask, request, jsonify
from pypdf import PdfReader
import tempfile
import io
import os
import asyncio
import httpx
from olmocr.data.renderpdf import render_pdf_to_base64png

app = Flask(__name__, static_folder=os.path.dirname(__file__), static_url_path="")

@app.route('/')
def root():
    return app.send_static_file('index.html')

def _page_count(data: bytes) -> int:
    """Return number of pages using PdfReader."""
    reader = PdfReader(io.BytesIO(data))
    return len(reader.pages)


async def _ocr_page(tmp_path: str, page_num: int) -> str:
    """Call local OCR service on a single page image."""
    # render the PDF page to base64 PNG using helper from olmocr
    b64 = await asyncio.to_thread(render_pdf_to_base64png, tmp_path, page_num, 1024)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post("http://127.0.0.1:8888/api/ocr", json={"image": b64})
            r.raise_for_status()
            return r.json().get("markdown", "")
    except Exception:
        return ""


async def _process_pdf(file):
    data = await asyncio.to_thread(file.read)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(data)
    try:
        page_total = await asyncio.to_thread(_page_count, data)
        tasks = [_ocr_page(tmp.name, i + 1) for i in range(page_total)]
        return await asyncio.gather(*tasks)
    finally:
        os.unlink(tmp.name)


@app.route('/api/ocr', methods=['POST'])
def api_ocr():
    file = request.files.get('pdf')
    if not file:
        return jsonify({'error': 'no file uploaded'}), 400
    try:
        pages = asyncio.run(_process_pdf(file))
        return jsonify({'pages': pages})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)

