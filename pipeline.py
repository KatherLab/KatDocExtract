from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from typing import List, Optional, Tuple, Dict, Any

from fastapi import HTTPException
from PIL import Image

from config import get_settings
from deepseek_client import run_deepseek_ocr, run_deepseek_text
from pdf_engine import PdfEngine
from models import (
    OCRImageObject, OCRPageDimensions, OCRPageObject,
    OCRRequest, OCRResponse, OCRTableObject, OCRUsageInfo,
)
from parse_utils import (
    extract_hyperlinks, extract_tables, html_table_to_markdown,
    replace_tables_inline_markdown, re_match_refdet, extract_coordinates_and_label
)

settings = get_settings()

DEFAULT_FIGURE_PROMPT = "Describe the figure in concise markdown."
_REFDET_PATTERN = re.compile(r"(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>)", re.DOTALL)

# --- Helper Functions (Ported from original to ensure exact parsing) ---

def _normalize_markdown(text: str) -> str:
    return text.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")

def _prompt_for_bbox(fmt) -> str:
    if not fmt or fmt.type == "text":
        return DEFAULT_FIGURE_PROMPT
    if fmt.type == "json_object":
        return "Return ONLY valid JSON (no markdown)."
    schema = fmt.json_schema.schema_definition if fmt.json_schema else {}
    return f"Return ONLY valid JSON following this schema:\n{json.dumps(schema)}"

def _scale_0_999_to_px(x: float, y: float, width: int, height: int) -> Tuple[int, int]:
    return int(x / 999.0 * width), int(y / 999.0 * height)

def _collect_image_regions(raw_text: str, width: int, height: int):
    """Parses DeepSeek <|ref|> tags to find bounding boxes."""
    regions = []
    for full, label_type, coords_str in re_match_refdet(raw_text):
        parsed = extract_coordinates_and_label((full, label_type, coords_str))
        if not parsed: continue
        label, boxes = parsed
        if label != "image": continue

        for x1, y1, x2, y2 in boxes:
            px1, py1 = _scale_0_999_to_px(x1, y1, width, height)
            px2, py2 = _scale_0_999_to_px(x2, y2, width, height)
            px1, px2 = sorted((max(0, px1), min(width, px2)))
            py1, py2 = sorted((max(0, py1), min(height, py2)))
            if px2 - px1 <= 1 or py2 - py1 <= 1: continue
            regions.append({"bbox": (px1, py1, px2, py2), "full_tag": full})
    return regions

def _apply_image_filters(regions, image_min_size: Optional[int], image_limit: Optional[int]):
    out = regions
    if image_min_size is not None:
        out = [r for r in out if (r["bbox"][2] - r["bbox"][0]) >= image_min_size and (r["bbox"][3] - r["bbox"][1]) >= image_min_size]
    if image_limit is not None and image_limit >= 0:
        out = sorted(out, key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]), reverse=True)[:image_limit]
    return out

def _replace_or_strip_refdet_tags(raw_text: str, images_out: List[OCRImageObject], inline_figure_text: bool) -> str:
    """Replaces tags with markdown descriptions or image links."""
    images_iter = iter(images_out)
    def repl(match: re.Match) -> str:
        label_type = match.group(2)
        if label_type != "image": return ""
        try:
            img = next(images_iter)
        except StopIteration: return ""
        
        if inline_figure_text:
            ann = (img.image_annotation or "").strip()
            if not ann: return ""
            return f"\n\n> **Figure** ({img.id})\n>\n" + "\n".join([f"> {ln}" if ln.strip() else ">" for ln in ann.splitlines()]) + "\n\n"
        else:
            return f"![{img.id}]({img.id})"
    return _REFDET_PATTERN.sub(repl, raw_text)

# --- Async Task Definitions ---

async def _process_figure_task(
    image: Image.Image, 
    img_id: str, 
    bbox: Tuple[int,int,int,int],
    prompt: str,
    model: str,
    req_base64: bool
) -> OCRImageObject:
    """Task to process a single figure crop via LLM."""
    annotation = await run_deepseek_ocr(image, prompt, model)
    
    img_b64 = None
    if req_base64:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        
    return OCRImageObject(
        id=img_id,
        top_left_x=bbox[0], top_left_y=bbox[1],
        bottom_right_x=bbox[2], bottom_right_y=bbox[3],
        image_annotation=annotation,
        image_base64=img_b64
    )

async def _process_full_page_ocr_task(
    image: Image.Image,
    page_index: int,
    req: OCRRequest,
    model: str
) -> Tuple[OCRPageObject, Optional[str]]:
    """
    Task to process a full page via Vision OCR. 
    Crucially, this now parses RefDet tags and recursively processes figures if needed.
    """
    raw_text = await run_deepseek_ocr(image, None, model)
    width, height = image.size
    
    # 1. Parse Regions from Raw Text
    regions = _collect_image_regions(raw_text, width, height)
    regions = _apply_image_filters(regions, req.image_min_size, req.image_limit)
    
    inline_figure_text = bool(req.inline_figure_text) if req.inline_figure_text is not None else True
    figure_prompt = (req.figure_prompt or "").strip() or _prompt_for_bbox(req.bbox_annotation_format)
    
    # 2. Process Figures (if any found in OCR output)
    images_out: List[OCRImageObject] = []
    sub_tasks = []
    
    for idx, region in enumerate(regions):
        x1, y1, x2, y2 = region["bbox"]
        crop = image.crop((x1, y1, x2, y2))
        
        # Logic: If we want inline text or specific formats, we MUST query the LLM again for the crop.
        # If we just want images, we skip the LLM call to save time, unless bbox_annotation_format enforces it.
        should_describe = inline_figure_text or (req.bbox_annotation_format is not None)
        
        img_id = f"img-{page_index}-{idx}.png"
        
        if should_describe:
            # Create sub-task
            t = _process_figure_task(crop, img_id, (x1,y1,x2,y2), figure_prompt, model, req.include_image_base64)
            sub_tasks.append(t)
        else:
            # Just create object without description
            img_b64 = None
            if req.include_image_base64:
                buf = io.BytesIO()
                crop.save(buf, format="PNG")
                img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            
            images_out.append(OCRImageObject(
                id=img_id,
                top_left_x=x1, top_left_y=y1, bottom_right_x=x2, bottom_right_y=y2,
                image_annotation=None, image_base64=img_b64
            ))

    # Execute sub-tasks for figures
    if sub_tasks:
        figure_results = await asyncio.gather(*sub_tasks)
        images_out.extend(figure_results)
    
    # Sort images by vertical position to match reading order (approximated by index usually, but good to be safe)
    images_out.sort(key=lambda x: (x.top_left_y or 0, x.top_left_x or 0))

    # 3. Clean Text (Replace tags)
    cleaned = _replace_or_strip_refdet_tags(raw_text, images_out, inline_figure_text)
    cleaned = _normalize_markdown(cleaned)
    
    # 4. Tables
    cleaned, tables = _process_tables(cleaned, page_index, req.table_format)
    
    page_obj = OCRPageObject(
        index=page_index,
        markdown=cleaned,
        images=images_out,
        tables=tables,
        hyperlinks=extract_hyperlinks(cleaned),
        dimensions=OCRPageDimensions(dpi=settings.PDF_TEXT_RENDER_DPI, width=width, height=height)
    )
    return page_obj, raw_text

def _process_tables(text: str, page_idx: int, fmt: Optional[str]) -> Tuple[str, List[OCRTableObject]]:
    tables = extract_tables(text)
    if not tables:
        if fmt is None: return replace_tables_inline_markdown(text), []
        return text, []

    out_tables = []
    updated_text = text
    
    if fmt is None:
        return replace_tables_inline_markdown(text), []

    for i, tbl_html in enumerate(tables):
        ext = "md" if fmt == "markdown" else "html"
        content = html_table_to_markdown(tbl_html) if fmt == "markdown" else tbl_html
        
        t_id = f"tbl-{page_idx}-{i}.{ext}"
        updated_text = updated_text.replace(tbl_html, f"\n[{t_id}]({t_id})\n", 1)
        out_tables.append(OCRTableObject(id=t_id, format=fmt, content=content))
        
    return updated_text, out_tables

# --- Main Pipeline Orchestrator ---

async def run_smart_pdf_pipeline(
    pdf_bytes: bytes,
    req: OCRRequest,
    model_name: str
) -> OCRResponse:
    
    engine = PdfEngine(pdf_bytes, settings.MIN_TEXT_DENSITY, settings.MAX_MOJIBAKE_RATIO)
    
    # Phase 1: Map (Analyze Pages & Generate Tasks)
    tasks = []
    page_results_map: Dict[int, Any] = {} 
    
    num_pages = engine.doc.page_count
    pages_to_process = req.pages if req.pages else range(num_pages)
    
    figure_prompt = (req.figure_prompt or "").strip() or _prompt_for_bbox(req.bbox_annotation_format)
    inline_figs = bool(req.inline_figure_text) if req.inline_figure_text is not None else True
    
    for i in pages_to_process:
        extraction = engine.analyze_and_extract(
            page_index=i,
            mode=req.pdf_mode or "auto",
            dpi=settings.PDF_TEXT_RENDER_DPI,
            image_min_size=req.image_min_size or 0,
            image_limit=req.image_limit if req.image_limit is not None else -1
        )
        
        page_results_map[i] = {
            "strategy": extraction.strategy_used,
            "dims": (extraction.width, extraction.height),
            "base_text_parts": [],
            "images": [],
            "tables": [],
            "raw": None
        }
        
        if extraction.strategy_used == "ocr_fallback":
            # Schedule Full Page OCR (which now handles RefDet recursively)
            t = _process_full_page_ocr_task(extraction.full_page_image, i, req, model_name)
            tasks.append(("page_ocr", i, t))
            
        else:
            # Text Layer Strategy - Schedule Figure OCRs
            text_flow = []
            img_count = 0
            
            for elem in extraction.elements:
                if elem.kind == "text":
                    text_flow.append({"type": "text", "content": elem.text})
                elif elem.kind == "image":
                    img_id = f"img-{i}-{img_count}.png"
                    img_count += 1
                    
                    t = _process_figure_task(
                        elem.image, img_id, elem.bbox_px, 
                        figure_prompt, model_name, 
                        req.include_image_base64
                    )
                    tasks.append(("fig_ocr", i, t, img_id))
                    text_flow.append({"type": "image_ref", "id": img_id})
            
            page_results_map[i]["flow"] = text_flow

    engine.close()

    # Phase 2: Execute All Tasks Concurrently
    coroutines = [t[2] for t in tasks]
    if coroutines:
        results = await asyncio.gather(*coroutines)
    else:
        results = []

    # Phase 3: Reduce (Assemble Results)
    for task_info, result in zip(tasks, results):
        t_type = task_info[0]
        p_idx = task_info[1]
        
        if t_type == "page_ocr":
            page_obj, raw = result
            page_results_map[p_idx]["final_obj"] = page_obj
            page_results_map[p_idx]["raw"] = raw
            
        elif t_type == "fig_ocr":
            img_obj = result
            page_results_map[p_idx]["images"].append(img_obj)

    # Final Assembly
    final_pages: List[OCRPageObject] = []
    
    for i in sorted(page_results_map.keys()):
        data = page_results_map[i]
        
        if "final_obj" in data:
            final_pages.append(data["final_obj"])
            continue
            
        # Assemble Text Layer Page
        md_parts = []
        images_lookup = {img.id: img for img in data["images"]}
        
        for item in data.get("flow", []):
            if item["type"] == "text":
                md_parts.append(item["content"])
            elif item["type"] == "image_ref":
                img_id = item["id"]
                img_obj = images_lookup.get(img_id)
                if not img_obj: continue
                
                if inline_figs:
                    ann = (img_obj.image_annotation or "").strip()
                    if ann:
                        # Format as blockquote figure
                        lines = ann.splitlines()
                        formatted = f"> **Figure**\n>\n" + "\n".join(f"> {l}" for l in lines)
                        md_parts.append(formatted)
                else:
                    md_parts.append(f"![Figure]({img_id})")

        full_md = "\n\n".join(md_parts)
        full_md = _normalize_markdown(full_md)
        
        w, h = data["dims"]
        p_obj = OCRPageObject(
            index=i,
            markdown=full_md,
            images=data["images"],
            tables=[],
            hyperlinks=extract_hyperlinks(full_md),
            dimensions=OCRPageDimensions(dpi=settings.PDF_TEXT_RENDER_DPI, width=w, height=h)
        )
        final_pages.append(p_obj)

    # Phase 4: Document Annotation
    doc_annotation = None
    if req.document_annotation_format:
        combined_text = "\n\n".join([p.markdown for p in final_pages])
        prompt = "Extract information."
        if req.document_annotation_format.type == "json_object":
             prompt = "Return valid JSON."
        doc_annotation = await run_deepseek_text(model_name, combined_text[:100000], prompt)

    usage = OCRUsageInfo(doc_size_bytes=len(pdf_bytes), pages_processed=len(final_pages))
    
    return OCRResponse(
        model=model_name,
        pages=final_pages,
        usage_info=usage,
        document_annotation=doc_annotation,
        raw_model_output=None
    )

async def run_images_pipeline(
    images: List[Image.Image],
    req: OCRRequest,
    model_name: str,
    doc_size: int
) -> OCRResponse:
    """Fallback pipeline for pure images."""
    tasks = []
    for i, img in enumerate(images):
        tasks.append(_process_full_page_ocr_task(img, i, req, model_name))
        
    results = await asyncio.gather(*tasks)
    
    pages = [r[0] for r in results]
    pages.sort(key=lambda p: p.index)
    
    return OCRResponse(
        model=model_name,
        pages=pages,
        usage_info=OCRUsageInfo(doc_size_bytes=doc_size, pages_processed=len(pages)),
        document_annotation=None
    )
