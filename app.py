from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, File, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import tomllib

from config import get_settings
from document_io import get_document_bytes_mime_ext, load_document_as_images
from files_api import router as files_router
from files_store import LocalDirFileStore
from models import OCRRequest, OCRResponse
from pipeline import run_smart_pdf_pipeline, run_images_pipeline

settings = get_settings()

app = FastAPI(title="DeepSeek-OCR Mistral-Compatible Wrapper")

app.state.file_store = LocalDirFileStore(settings.FILES_DIR)
app.include_router(files_router)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Read app version from pyproject.toml
_pyproject = tomllib.loads((BASE_DIR / "pyproject.toml").read_text())
APP_VERSION = _pyproject["project"]["version"]

# Expose version in all templates
templates.env.globals["app_version"] = APP_VERSION
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_FILE_CONTENT_URL_RE = re.compile(r"/v1/files/([0-9a-fA-F-]{36})/content")

def _maybe_resolve_local_files_content_url_to_data_doc(url: str) -> Optional[dict]:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    m = _FILE_CONTENT_URL_RE.search(url)
    if not m: return None
    file_id = m.group(1)
    try:
        content, meta = app.state.file_store.read_bytes(file_id)
    except Exception: return None
    if meta.get("deleted"):
        raise HTTPException(status_code=404, detail="File was deleted.")
    mime = (meta.get("mimetype") or "application/octet-stream").split(";")[0].strip().lower()
    filename = meta.get("filename") or "uploaded.file"
    b64 = base64.b64encode(content).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"
    name_l = filename.lower()
    is_image = mime.startswith("image/") or any(
        name_l.endswith("." + e) for e in ["png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"]
    )
    if is_image:
        return {"type": "image_url", "image_url": {"url": data_url}}
    return {"type": "document_url", "document_url": data_url, "document_name": filename}

def _unwrap_image_url(doc_dict: dict) -> Optional[str]:
    if doc_dict.get("type") != "image_url": return None
    image_url = doc_dict.get("image_url")
    if isinstance(image_url, dict): return image_url.get("url")
    if isinstance(image_url, str): return image_url
    return None

def _parse_optional_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if s == "": return None
    try: return int(s)
    except ValueError: raise HTTPException(status_code=400, detail=f"Invalid int: {s}")

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/v1/ocr", response_model=OCRResponse)
async def ocr_endpoint(req: OCRRequest):
    model_name = settings.DEFAULT_MODEL_NAME
    doc_dict = req.document.model_dump() if hasattr(req.document, "model_dump") else req.document

    # file_id resolution
    if getattr(req.document, "type", None) == "file" or (isinstance(doc_dict, dict) and doc_dict.get("type") == "file"):
        file_id = doc_dict.get("file_id") if isinstance(doc_dict, dict) else getattr(req.document, "file_id", None)
        if not file_id:
            raise HTTPException(status_code=400, detail="document.file_id is required for type='file'.")
        content, meta = app.state.file_store.read_bytes(file_id)
        if meta.get("deleted"):
            raise HTTPException(status_code=404, detail="File was deleted.")
        mime = (meta.get("mimetype") or "application/octet-stream").split(";")[0].strip().lower()
        filename = meta.get("filename") or "uploaded.file"
        b64 = base64.b64encode(content).decode("utf-8")
        data_url = f"data:{mime};base64,{b64}"
        name_l = filename.lower()
        is_image = mime.startswith("image/") or any(name_l.endswith("." + e) for e in ["png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"])
        doc_dict = {"type": "image_url", "image_url": {"url": data_url}} if is_image else {"type": "document_url", "document_url": data_url, "document_name": filename}

    # resolve local content urls
    if isinstance(doc_dict, dict):
        if doc_dict.get("type") == "document_url":
            resolved = _maybe_resolve_local_files_content_url_to_data_doc(doc_dict.get("document_url", ""))
            if resolved: doc_dict = resolved
        elif doc_dict.get("type") == "image_url":
            url = _unwrap_image_url(doc_dict) or ""
            resolved = _maybe_resolve_local_files_content_url_to_data_doc(url)
            if resolved: doc_dict = resolved

    data, mime, ext = get_document_bytes_mime_ext(doc_dict)
    is_pdf = (mime == "application/pdf" or ext == "pdf")

    # --- Pipeline Execution ---
    if is_pdf:
        return await run_smart_pdf_pipeline(data, req, model_name)

    images, size, kind = load_document_as_images(doc_dict)
    return await run_images_pipeline(images, req, model_name, size)

@app.post("/", response_class=HTMLResponse)
async def upload_page(
    request: Request,
    file: UploadFile = File(...),
    advanced: bool = Form(False),
    include_image_base64: bool = Form(False),
    extract_header: bool = Form(False),
    extract_footer: bool = Form(False),
    table_format: str = Form(""),
    image_min_size: str = Form(""),
    image_limit: str = Form(""),
    pdf_mode: str = Form("auto"),
    inline_figure_text: bool = Form(True),
    figure_prompt: str = Form(""),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    filename = file.filename or "uploaded file"
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    name_l = filename.lower()

    is_pdf = content_type == "application/pdf" or name_l.endswith(".pdf")
    is_image = content_type.startswith("image/") or any(name_l.endswith("." + e) for e in ["png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"])

    mime = content_type or "application/octet-stream"
    b64 = base64.b64encode(content).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    doc = {"type": "image_url", "image_url": {"url": data_url}} if is_image else {"type": "document_url", "document_url": data_url, "document_name": filename}

    if not advanced:
        include_image_base64 = False
        extract_header = False
        extract_footer = False
        table_format = ""
        image_min_size = ""
        image_limit = ""
        pdf_mode = "auto"
        inline_figure_text = True
        figure_prompt = ""

    req = OCRRequest(
        document=doc,
        model=settings.DEFAULT_MODEL_NAME,
        include_image_base64=include_image_base64,
        extract_header=extract_header,
        extract_footer=extract_footer,
        table_format=(table_format or None),
        image_min_size=_parse_optional_int(image_min_size),
        image_limit=_parse_optional_int(image_limit),
        pages=None,
        include_raw=False,
        pdf_mode=(pdf_mode or "auto"),
        extract_figures=True,
        inline_figure_text=inline_figure_text,
        figure_prompt=(figure_prompt or None),
    )

    resp = await ocr_endpoint(req)

    joiner = "\n\n---\n\n" if settings.MARKDOWN_PAGE_BREAKS else "\n\n"
    combined_md = joiner.join([p.markdown for p in resp.pages]).strip()

    original_view = None
    if is_pdf:
        original_view = {"type": "pdf", "src": f"data:application/pdf;base64,{b64}#toolbar=1&navpanes=0"}
    elif is_image:
        original_view = {"type": "img", "src": f"data:{mime};base64,{b64}"}
    else:
        original_view = {"type": "other"}

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "filename": filename,
            "pages_count": resp.usage_info.pages_processed,
            "original_view": original_view,
            "combined_md": combined_md.replace("</textarea", "</text-area"),
        },
    )
