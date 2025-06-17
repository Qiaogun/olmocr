import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Dict, Any
import uuid

import tornado.web
import tornado.websocket
import tornado.ioloop
import tornado.httpclient
from pypdf import PdfReader

from olmocr_anchor import get_anchor_text

# Configuration
API_ENDPOINT = "http://192.0.2.3:6000/v1/chat/completions"
MODEL_LIST_ENDPOINT = "http://192.0.2.3:6000/v1/models"
MODEL_NAME = "olmOCR-7B"

PROMPT_TEMPLATE = (
    "Below is the image of one page of a document, as well as some raw textual content that was previously extracted for it. "
    "Just return the plain text representation of this document as if you were reading it naturally.\n"
    "Do not hallucinate.\n"
    "RAW_TEXT_START\n%s\nRAW_TEXT_END"
)

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def estimate_tokens(text: str) -> int:
    """Rough token estimation - approximately 4 characters per token"""
    return len(text) // 4

async def is_olmocr_available() -> bool:
    """Check if OlmOCR model is available using async HTTP client"""
    try:
        http_client = tornado.httpclient.AsyncHTTPClient()
        response = await http_client.fetch(
            MODEL_LIST_ENDPOINT,
            method="GET",
            request_timeout=5
        )
        j = json.loads(response.body)['data']
        return any(i['id'] == MODEL_NAME for i in j)
    except Exception as e:
        log.error(f"Error checking OlmOCR availability: {e}")
        return False
    finally:
        http_client.close()

class OCRWebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file_chunks = []
        self.total_bytes_received = 0
        self.temp_file_path = None
        self.session_id = str(uuid.uuid4())
        self.http_client = tornado.httpclient.AsyncHTTPClient()
        self.current_page = 0
        self.total_pages = 0

    def check_origin(self, origin):
        return True  # Allow all origins for development

    def open(self):
        log.info(f"WebSocket connection opened: {self.session_id}")

    async def on_message(self, message):
        try:
            data = json.loads(message)
            
            # Handle different message formats
            if "fileChunk" in data:
                await self.handle_file_chunk(data)
            elif "endOfFile" in data:
                await self.handle_end_of_file(data)
            elif "continue" in data:
                await self.handle_continue(data)
            else:
                # Try the original format with "type" field
                message_type = data.get("type")
                if message_type == "fileChunk":
                    await self.handle_file_chunk(data)
                elif message_type == "endOfFile":
                    await self.handle_end_of_file(data)
                else:
                    log.warning(f"Unknown message format: {data}")

        except json.JSONDecodeError:
            log.error("Invalid JSON received")
            await self.send_error("Invalid JSON format")
        except Exception as e:
            log.error(f"Error handling message: {e}")
            await self.send_error(f"Error processing message: {str(e)}")

    async def handle_continue(self, data):
        """Handle continue message to process next page or exit"""
        should_continue = data.get("continue", False)
        
        if should_continue:
            # Continue to next page
            self.current_page += 1
            if self.current_page <= self.total_pages:
                await self.process_page(self.current_page, self.total_pages)
            else:
                # No more pages, send completion
                await self.send_message({
                    "type": "request_complete",
                    "data": {
                        "page": self.total_pages,
                        "totalPages": self.total_pages
                    }
                })
        else:
            # User chose to exit, send completion immediately
            await self.send_message({
                "type": "request_complete",
                "data": {
                    "page": self.current_page,
                    "totalPages": self.total_pages
                }
            })

    async def handle_file_chunk(self, data):
        """Handle incoming file chunk"""
        # Handle both formats
        chunk_data = data.get("fileChunk") or data.get("data", "")
        chunk_bytes = base64.b64decode(chunk_data)
        self.file_chunks.append(chunk_bytes)
        self.total_bytes_received += len(chunk_bytes)

        # Send progress update
        await self.send_message({
            "type": "progress",
            "data": {
                "message": f"Received {self.total_bytes_received} bytes",
                "uploaded_bytes": self.total_bytes_received,
                "progress": "in-progress"
            }
        })

    async def handle_end_of_file(self, data):
        """Handle end of file and start processing"""
        try:
            # Combine all chunks into a single file
            file_data = b''.join(self.file_chunks)
            
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                temp_file.write(file_data)
                self.temp_file_path = temp_file.name

            await self.send_message({
                "type": "progress",
                "data": {
                    "message": f"File received successfully: {len(file_data)} bytes",
                    "progress": "complete"
                }
            })

            # Start document processing
            await self.process_document()

        except Exception as e:
            log.error(f"Error handling end of file: {e}")
            await self.send_error(f"Error processing file: {str(e)}")

    async def process_document(self):
        """Process the PDF document"""
        try:
            await self.send_message({
                "type": "progress",
                "data": {
                    "message": "Starting document processing... 1 page detected",
                    "page": 0,
                    "totalPages": 1
                }
            })

            # Get total number of pages
            reader = PdfReader(self.temp_file_path)
            self.total_pages = len(reader.pages)
            self.current_page = 0

            await self.send_message({
                "type": "progress",
                "data": {
                    "message": f"Starting document processing... {self.total_pages} page{'s' if self.total_pages > 1 else ''} detected",
                    "page": 0,
                    "totalPages": self.total_pages
                }
            })

            # Start with first page
            if self.total_pages > 0:
                self.current_page = 1
                await self.process_page(self.current_page, self.total_pages)
            else:
                # Send completion message for empty document
                await self.send_message({
                    "type": "request_complete",
                    "data": {
                        "page": 0,
                        "totalPages": 0
                    }
                })

        except Exception as e:
            log.error(f"Error processing document: {e}")
            await self.send_error(f"Error processing document: {str(e)}")

    async def process_page(self, page_num: int, total_pages: int):
        """Process a single page"""
        try:
            # Send page processing start
            await self.send_message({
                "type": "progress",
                "data": {
                    "message": "Starting communication with vLLM backend...",
                }
            })

            # Get anchor text
            anchor_text = get_anchor_text(self.temp_file_path, page_num, target_length=4000)
            
            # Build the full prompt
            prompt_text = PROMPT_TEMPLATE % anchor_text
            
            # Render page to base64 PNG
            image_base64 = await self.render_pdf_to_base64png(self.temp_file_path, page_num)
            
            # Send page image
            await self.send_message({
                "type": "page_image",
                "data": {
                    "page": page_num,
                    "image": image_base64
                }
            })

            # Start the OCR request and token counting concurrently
            ocr_task = asyncio.create_task(self.ocr_page_with_retry(page_num, anchor_text, image_base64))
            token_task = asyncio.create_task(self.simulate_token_progress(page_num))
            
            # Wait for OCR to complete and stop token counting
            response_data = await ocr_task
            token_task.cancel()
            
            # Send debug_progress message before page_complete
            await self.send_message({
                "type": "debug_progress",
                "data": {
                    "page": page_num,
                    "debug_info": {
                        "primary_language": response_data.get("primary_language", "en"),
                        "is_rotation_valid": response_data.get("is_rotation_valid", True),
                        "rotation_correction": response_data.get("rotation_correction", 0),
                        "is_table": response_data.get("is_table", False),
                        "is_diagram": response_data.get("is_diagram", False),
                        "natural_text": response_data.get("natural_text", "")
                    }
                }
            })
            
            # Send page complete with correct format
            await self.send_message({
                "type": "page_complete",
                "data": {
                    "page": page_num,
                    "response": response_data,
                    "prompt_text": prompt_text
                }
            })

        except Exception as e:
            log.error(f"Error processing page {page_num}: {e}")
            await self.send_error(f"Error processing page {page_num}: {str(e)}")

    async def simulate_token_progress(self, page_num: int):
        """Simulate token progress like the original website"""
        try:
            current_tokens = 30  # Start at 30 like in the screenshot
            
            while True:
                await self.send_message({
                    "type": "token_progress",
                    "data": {
                        "page": page_num,
                        "tokens": current_tokens
                    }
                })
                
                current_tokens += 10  # Increment by 10 each time
                await asyncio.sleep(0.14)  # Wait ~140ms like in the original
                
        except asyncio.CancelledError:
            # Task was cancelled, OCR is done
            pass

    async def ocr_page_with_retry(self, page_num: int, anchor_text: str, image_base64: str, max_retries: int = 3) -> Dict[str, Any]:
        """OCR a page with retry logic"""
        prompt = PROMPT_TEMPLATE % anchor_text
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer LAIN",
        }
        
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                    ]
                }
            ],
            "temperature": 0.8,
            "max_tokens": 4096,
        }

        for attempt in range(max_retries):
            try:
                # Create HTTP request
                request = tornado.httpclient.HTTPRequest(
                    url=API_ENDPOINT,
                    method="POST",
                    headers=headers,
                    body=json.dumps(payload),
                    request_timeout=60
                )
                
                # Make the async API request
                response = await self.http_client.fetch(request)
                
                if response.code == 200:
                    result = json.loads(response.body)
                    content = result["choices"][0]["message"]["content"]
                    
                    # Try to parse as JSON
                    try:
                        response_data = json.loads(content)
                        return response_data
                    except json.JSONDecodeError as e:
                        log.warning(f"JSON decode failed on attempt {attempt + 1}: {e}")
                        if attempt == max_retries - 1:
                            # Return a fallback response
                            return {
                                "natural_text": content,
                                "primary_language": "en",
                                "is_rotation_valid": True,
                                "rotation_correction": 0,
                                "is_table": False,
                                "is_diagram": False
                            }
                        continue
                else:
                    raise Exception(f"API request failed: {response.code} {response.body}")
                    
            except Exception as e:
                log.error(f"OCR attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise

        raise Exception("All OCR attempts failed")

    async def render_pdf_to_base64png(self, local_pdf_path: str, page_num: int, target_longest_image_dim: int = 2048) -> str:
        """Render PDF page to base64 PNG"""
        # Get page dimensions
        reader = PdfReader(local_pdf_path)
        page = reader.pages[page_num - 1]
        mediabox = page.mediabox
        width = float(mediabox[2] - mediabox[0])
        height = float(mediabox[3] - mediabox[1])
        longest_dim = max(width, height)

        # Convert PDF page to PNG using pdftoppm
        process = await asyncio.create_subprocess_exec(
            "pdftoppm", "-png", "-f", str(page_num), "-l", str(page_num),
            "-r", str(target_longest_image_dim * 72 / longest_dim),
            local_pdf_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        
        if process.returncode != 0:
            raise Exception(f"pdftoppm failed: {stderr.decode('utf-8')}")
            
        return base64.b64encode(stdout).decode("utf-8")

    async def send_message(self, data):
        """Send a message to the client"""
        try:
            await self.write_message(json.dumps(data))
        except Exception as e:
            log.error(f"Error sending message: {e}")

    async def send_error(self, error_message):
        """Send an error message to the client"""
        await self.send_message({
            "type": "error",
            "data": {
                "message": error_message
            }
        })

    def on_close(self):
        """Clean up when connection closes"""
        log.info(f"WebSocket connection closed: {self.session_id}")
        self.cleanup()

    def cleanup(self):
        """Clean up temporary files and HTTP client"""
        if self.temp_file_path and os.path.exists(self.temp_file_path):
            try:
                os.unlink(self.temp_file_path)
                log.info(f"Cleaned up temporary file: {self.temp_file_path}")
            except Exception as e:
                log.error(f"Error cleaning up temporary file: {e}")
        
        # Close HTTP client
        if hasattr(self, 'http_client'):
            self.http_client.close()

class StaticFileHandler(tornado.web.StaticFileHandler):
    """Serve static files"""
    pass

def make_app():
    return tornado.web.Application([
        (r"/api/ws", OCRWebSocketHandler),
        (r"/(.*)", StaticFileHandler, {"path": "./static", "default_filename": "index.html"}),
    ], debug=True)

async def main():
    # Check if OlmOCR is available
    if not await is_olmocr_available():
        log.warning("OlmOCR model not available at the configured endpoint")
    else:
        log.info("OlmOCR model is available")

    app = make_app()
    app.listen(8888)
    log.info("Server started on http://localhost:8888")
    log.info("WebSocket endpoint: ws://localhost:8888/api/ws")
    
    # Keep the server running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
