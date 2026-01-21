from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class JsonSchema(BaseModel):
    name: str
    schema_definition: Dict[str, Any]
    description: Optional[str] = None
    strict: bool = False


class ResponseFormat(BaseModel):
    type: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: Optional[JsonSchema] = None


class OCRImageObject(BaseModel):
    id: str
    top_left_x: Optional[int] = None
    top_left_y: Optional[int] = None
    bottom_right_x: Optional[int] = None
    bottom_right_y: Optional[int] = None
    image_annotation: Optional[str] = None
    image_base64: Optional[str] = None


class OCRTableObject(BaseModel):
    id: str
    format: Literal["markdown", "html"]
    content: str


class OCRPageDimensions(BaseModel):
    dpi: int
    width: int
    height: int


class OCRPageObject(BaseModel):
    index: int
    markdown: str
    images: List[OCRImageObject] = Field(default_factory=list)
    tables: List[OCRTableObject] = Field(default_factory=list)
    hyperlinks: List[str] = Field(default_factory=list)
    header: Optional[str] = None
    footer: Optional[str] = None
    dimensions: Optional[OCRPageDimensions] = None


class OCRUsageInfo(BaseModel):
    doc_size_bytes: Optional[int] = None
    pages_processed: int


class FileChunk(BaseModel):
    type: Literal["file"] = "file"
    file_id: str


class DocumentURLChunk(BaseModel):
    type: Literal["document_url"] = "document_url"
    document_url: str
    document_name: Optional[str] = None


class ImageURLChunk(BaseModel):
    type: Literal["image_url"] = "image_url"
    image_url: Union[str, Dict[str, Any]]


DocumentChunk = Union[FileChunk, DocumentURLChunk, ImageURLChunk]


class OCRRequest(BaseModel):
    document: DocumentChunk
    model: Optional[str] = None

    bbox_annotation_format: Optional[ResponseFormat] = None
    document_annotation_format: Optional[ResponseFormat] = None

    id: Optional[str] = None

    image_limit: Optional[int] = None
    image_min_size: Optional[int] = None
    include_image_base64: Optional[bool] = None

    pages: Optional[List[int]] = None

    extract_header: bool = False
    extract_footer: bool = False

    table_format: Optional[Literal["markdown", "html"]] = None
    include_raw: bool = False

    # PDF behavior
    pdf_mode: Optional[Literal["auto", "ocr", "text"]] = "auto"
    pdf_text_min_chars: Optional[int] = None
    pdf_text_sample_pages: Optional[int] = None
    extract_figures: Optional[bool] = True

    # NEW: replace images with parsed figure text in markdown
    inline_figure_text: Optional[bool] = True
    figure_prompt: Optional[str] = None


class OCRResponse(BaseModel):
    model: str
    pages: List[OCRPageObject]
    usage_info: OCRUsageInfo
    document_annotation: Optional[str] = None
    raw_model_output: Optional[str] = None


# ---------------------------
# Files API models (Mistral-compatible)
# ---------------------------

Purpose = Literal["fine-tune", "batch", "ocr"]
SampleType = Literal["pretrain", "instruct", "batch_request", "batch_result", "batch_error"]
FileSource = Literal["upload", "repository", "mistral"]


class FileSchema(BaseModel):
    id: str
    object: Literal["file"] = "file"

    bytes: Optional[int] = None
    created_at: int
    filename: str

    purpose: Purpose = "ocr"
    sample_type: Optional[SampleType] = None
    source: FileSource = "upload"

    num_lines: Optional[int] = None
    mimetype: Optional[str] = None
    signature: Optional[str] = None

    deleted: Optional[bool] = None


class FileListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: List[FileSchema]
    total: Optional[int] = None


class DeleteFileResponse(BaseModel):
    id: str
    object: Literal["file"] = "file"
    deleted: bool


class SignedURLResponse(BaseModel):
    url: str
