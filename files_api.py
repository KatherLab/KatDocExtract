from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from files_signing import make_token, signing_enabled, verify_token
from files_store import FileStore
from models import DeleteFileResponse, FileListResponse, FileSchema, SignedURLResponse

router = APIRouter()


def get_store(request: Request) -> FileStore:
    store = getattr(request.app.state, "file_store", None)
    if store is None:
        raise HTTPException(status_code=500, detail="Files store is not configured.")
    return store


def _normalize_upload_file(
    file: Optional[UploadFile],
    content: Optional[UploadFile],
) -> UploadFile:
    up = file or content
    if up is None:
        raise HTTPException(status_code=400, detail="Missing uploaded file field (expected 'file' or 'content').")
    return up


@router.get("/v1/files", response_model=FileListResponse)
def list_files(
    page: int = Query(0, ge=0),
    page_size: int = Query(100, ge=1, le=500),
    store: FileStore = Depends(get_store),
):
    all_files = store.list_files()
    total = len(all_files)

    start = page * page_size
    end = start + page_size
    data = all_files[start:end]

    return FileListResponse(
        data=[FileSchema(**m) for m in data],
        total=total,
    )


@router.post("/v1/files", response_model=FileSchema)
async def upload_file(
    # tolerate both multipart field names commonly used by clients
    file: Optional[UploadFile] = File(None),
    content: Optional[UploadFile] = File(None),
    file_name: Optional[str] = Form(None),
    purpose: str = Form("ocr"),
    sample_type: Optional[str] = Form(None),
    store: FileStore = Depends(get_store),
):
    up = _normalize_upload_file(file, content)
    raw = await up.read()

    filename = file_name or up.filename or "uploaded.file"
    mimetype = (up.content_type or "").split(";")[0].strip().lower() or None

    meta = store.save_upload(
        filename=filename,
        content=raw,
        mimetype=mimetype,
        purpose=purpose,
        sample_type=sample_type,
        source="upload",
    )
    return FileSchema(**meta)


@router.get("/v1/files/{file_id}", response_model=FileSchema)
def retrieve_file(file_id: str, store: FileStore = Depends(get_store)):
    sf = store.get(file_id)
    return FileSchema(**sf.meta)


@router.delete("/v1/files/{file_id}", response_model=DeleteFileResponse)
def delete_file(file_id: str, store: FileStore = Depends(get_store)):
    res = store.delete(file_id)
    return DeleteFileResponse(**res)


@router.get("/v1/files/{file_id}/content")
def download_file(
    file_id: str,
    expires: Optional[int] = Query(None),
    token: Optional[str] = Query(None),
    store: FileStore = Depends(get_store),
):
    # If either signed param is present, enforce both.
    if expires is not None or token is not None:
        if expires is None or token is None:
            raise HTTPException(status_code=400, detail="Signed download requires both expires and token.")
        verify_token(file_id=file_id, expires_at=int(expires), token=token)

    sf = store.get(file_id)
    if sf.meta.get("deleted"):
        raise HTTPException(status_code=404, detail="File was deleted.")

    media_type = sf.meta.get("mimetype") or "application/octet-stream"
    filename = sf.meta.get("filename") or "download.bin"

    return FileResponse(
        path=str(sf.path),
        media_type=media_type,
        filename=filename,
    )


@router.get("/v1/files/{file_id}/url", response_model=SignedURLResponse)
def get_signed_url(
    request: Request,
    file_id: str,
    expiry: int = Query(24, ge=1, le=168),  # hours
    store: FileStore = Depends(get_store),
):
    # verify file exists
    sf = store.get(file_id)
    if sf.meta.get("deleted"):
        raise HTTPException(status_code=404, detail="File was deleted.")

    base = str(request.base_url).rstrip("/")

    # If signing is not configured, return a plain URL to /content
    if not signing_enabled():
        return SignedURLResponse(url=f"{base}/v1/files/{file_id}/content")

    expires_at = int(time.time()) + int(expiry) * 3600
    token = make_token(file_id=file_id, expires_at=expires_at)

    url = f"{base}/v1/files/{file_id}/content?expires={expires_at}&token={token}"
    return SignedURLResponse(url=url)
