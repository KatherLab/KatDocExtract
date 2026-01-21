from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException
from PIL import Image, ImageOps

# We now use our internal engine for PDF rendering
from pdf_engine import PdfEngine
from config import get_settings

settings = get_settings()

def load_image_from_bytes(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        return image.convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load image: {e}")

def decode_data_url(data_url: str) -> Tuple[bytes, str]:
    if not data_url.startswith("data:"):
        raise HTTPException(status_code=400, detail="Data URL must start with 'data:'")
    try:
        header, b64data = data_url.split(",", 1)
        mime = header.split(";")[0].split(":", 1)[1]
        raw = base64.b64decode(b64data)
        return raw, mime
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid data URL: {e}")

def fetch_url(url: str) -> Tuple[bytes, str]:
    try:
        resp = requests.get(url, timeout=60)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: HTTP {resp.status_code}")
    content_type = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
    return resp.content, (content_type or "application/octet-stream")

def guess_ext_from_mime(mime: str) -> Optional[str]:
    mime = (mime or "").lower()
    if mime == "application/pdf": return "pdf"
    if mime.startswith("image/"): return mime.split("/", 1)[1].replace("jpeg", "jpg")
    if "wordprocessingml" in mime or "msword" in mime: return "docx" if "wordprocessingml" in mime else "doc"
    if "presentationml" in mime or "powerpoint" in mime: return "pptx" if "presentationml" in mime else "ppt"
    return None

def guess_ext_from_url(url: str) -> Optional[str]:
    import re
    m = re.search(r"\.([a-zA-Z0-9]+)(\?|#|$)", url)
    return m.group(1).lower() if m else None

def convert_office_to_pdf_bytes(data: bytes, ext: str) -> bytes:
    """
    Plugin-style function for LibreOffice conversion.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise HTTPException(
            status_code=500,
            detail="Office conversion requires LibreOffice installed on the server.",
        )

    with tempfile.TemporaryDirectory(prefix="ocr_office_") as td:
        in_path = os.path.join(td, f"input.{ext}")
        with open(in_path, "wb") as f:
            f.write(data)

        # Run conversion
        cmd = [
            soffice, "--headless", "--nologo", "--nofirststartwizard",
            "--convert-to", "pdf", "--outdir", td, in_path
        ]
        
        try:
            # We use a distinct process group or env to avoid zombie processes
            subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=120, env={**os.environ, "HOME": td}
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="LibreOffice conversion timed out.")
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"LibreOffice failed: {e.stderr.decode()[:200]}")

        # Find output
        for f in os.listdir(td):
            if f.lower().endswith(".pdf"):
                with open(os.path.join(td, f), "rb") as pdf_file:
                    return pdf_file.read()
        
        raise HTTPException(status_code=500, detail="No PDF produced by LibreOffice.")

def get_document_bytes_mime_ext(document: Dict[str, Any]) -> Tuple[bytes, str, Optional[str]]:
    doc_type = document.get("type")
    url = None
    
    if doc_type == "image_url":
        url = document.get("image_url")
        if isinstance(url, dict): url = url.get("url")
    elif doc_type == "document_url":
        url = document.get("document_url")
    elif doc_type == "file":
        # Handled by app.py resolver before calling this
        pass
    
    if not url:
         raise HTTPException(status_code=400, detail="Missing URL in document object.")

    if url.startswith("data:"):
        data, mime = decode_data_url(url)
        ext = guess_ext_from_mime(mime)
        return data, mime, ext

    data, mime = fetch_url(url)
    ext = guess_ext_from_mime(mime) or guess_ext_from_url(url)
    return data, mime, ext

def load_document_as_images(document: Dict[str, Any], dpi: int = 150) -> Tuple[List[Image.Image], int, str]:
    """
    Loads document and returns a list of PIL Images (one per page).
    Handles PDF (via fitz) and Office (via conversion).
    """
    data, mime, ext = get_document_bytes_mime_ext(document)
    doc_size = len(data)
    
    mime_l = (mime or "").lower()
    ext_l = (ext or "").lower()

    # Image
    if mime_l.startswith("image/") or ext_l in ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"]:
        return [load_image_from_bytes(data)], doc_size, "image"

    # Office -> PDF
    if ext_l in ["doc", "docx", "ppt", "pptx", "odt", "odp"] or "officedocument" in mime_l or "msword" in mime_l:
        data = convert_office_to_pdf_bytes(data, ext_l or "bin")
        # Fallthrough to PDF handling

    # PDF
    try:
        eng = PdfEngine(data, settings.MIN_TEXT_DENSITY, settings.MAX_MOJIBAKE_RATIO)
        images = []
        for i in range(eng.doc.page_count):
            images.append(eng._render_page(eng.doc.load_page(i), dpi))
        eng.close()
        return images, doc_size, "pdf"
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process document: {e}")
