from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from fastapi import HTTPException
from PIL import Image
import fitz  # PyMuPDF

from heuristics import PageAnalyzer

@dataclass
class PdfElement:
    kind: str  # "text" | "image"
    bbox_px: Tuple[int, int, int, int]
    text: Optional[str] = None
    image: Optional[Image.Image] = None

@dataclass
class PageExtractionResult:
    page_index: int
    strategy_used: str # "text_layer" or "ocr_fallback"
    elements: List[PdfElement]
    full_page_image: Optional[Image.Image] # Present if OCR fallback was needed
    width: int
    height: int
    dpi: int

class PdfEngine:
    def __init__(self, pdf_bytes: bytes, min_density: float, max_mojibake: float):
        try:
            self.doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid PDF file: {e}")
            
        self.analyzer = PageAnalyzer(min_density, max_mojibake)

    def close(self):
        if self.doc:
            self.doc.close()

    def _render_page(self, page, dpi: int) -> Image.Image:
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        # Create PIL image from bytes
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img

    def _rect_to_px(self, bbox, dpi: int) -> Tuple[int, int, int, int]:
        x0, y0, x1, y1 = bbox
        zoom = dpi / 72.0
        return int(x0 * zoom), int(y0 * zoom), int(x1 * zoom), int(y1 * zoom)

    def analyze_and_extract(
        self, 
        page_index: int, 
        mode: str = "auto", 
        dpi: int = 150,
        image_min_size: int = 0,
        image_limit: int = -1
    ) -> PageExtractionResult:
        """
        Decides strategy and extracts content for a single page.
        """
        page = self.doc.load_page(page_index)
        
        # 1. Get Geometry
        page_w_pt = page.rect.width
        page_h_pt = page.rect.height
        page_area_pt = page_w_pt * page_h_pt
        
        # 2. Extract Text Dict for Analysis & Extraction
        text_dict = page.get_text("dict")
        blocks = text_dict.get("blocks", [])
        
        # Calculate text area for heuristics
        text_area_sum = 0.0
        raw_text_accum = []
        
        for b in blocks:
            if b.get("type") == 0: # Text
                bbox = b.get("bbox", (0,0,0,0))
                area = (bbox[2]-bbox[0]) * (bbox[3]-bbox[1])
                text_area_sum += area
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        raw_text_accum.append(span.get("text", ""))
        
        full_text = "".join(raw_text_accum)
        
        # 3. Decide Strategy
        use_ocr = False
        if mode == "ocr":
            use_ocr = True
        elif mode == "text":
            use_ocr = False
        else: # auto
            analysis = self.analyzer.analyze(full_text, page_area_pt, text_area_sum)
            if analysis.is_scanned:
                use_ocr = True

        # 4. Execute Strategy
        elements: List[PdfElement] = []
        
        # Always render full page if we need OCR or if we need to crop images
        rendered_page = self._render_page(page, dpi)
        w, h = rendered_page.size

        if use_ocr:
            # Strategy: Full Page OCR
            return PageExtractionResult(
                page_index=page_index,
                strategy_used="ocr_fallback",
                elements=[],
                full_page_image=rendered_page,
                width=w, height=h, dpi=dpi
            )
        else:
            # Strategy: Hybrid (Text + Figures)
            for b in blocks:
                bbox_pt = b.get("bbox")
                bbox_px = self._rect_to_px(bbox_pt, dpi)
                
                if b.get("type") == 0: # Text
                    # Reconstruct text block
                    lines = []
                    for ln in b.get("lines", []):
                        line_text = "".join(s.get("text", "") for s in ln.get("spans", []))
                        if line_text.strip():
                            lines.append(line_text)
                    block_text = "\n".join(lines).strip()
                    if block_text:
                        elements.append(PdfElement("text", bbox_px, text=block_text))
                
                elif b.get("type") == 1: # Image
                    # Check size constraints
                    bw = bbox_px[2] - bbox_px[0]
                    bh = bbox_px[3] - bbox_px[1]
                    if bw < image_min_size or bh < image_min_size:
                        continue
                        
                    # Crop & Clamp coordinates
                    x1 = max(0, min(w, bbox_px[0]))
                    y1 = max(0, min(h, bbox_px[1]))
                    x2 = max(0, min(w, bbox_px[2]))
                    y2 = max(0, min(h, bbox_px[3]))
                    
                    if x2 > x1 and y2 > y1:
                        crop = rendered_page.crop((x1, y1, x2, y2))
                        elements.append(PdfElement("image", (x1,y1,x2,y2), image=crop))

            # Sort elements reading order (top-down, left-right)
            elements.sort(key=lambda e: (e.bbox_px[1], e.bbox_px[0]))
            
            # Apply image limit if needed (keep largest)
            if image_limit >= 0:
                imgs = [e for e in elements if e.kind == "image"]
                txts = [e for e in elements if e.kind == "text"]
                imgs.sort(key=lambda e: (e.bbox_px[2]-e.bbox_px[0])*(e.bbox_px[3]-e.bbox_px[1]), reverse=True)
                imgs = imgs[:image_limit]
                elements = sorted(txts + imgs, key=lambda e: (e.bbox_px[1], e.bbox_px[0]))

            return PageExtractionResult(
                page_index=page_index,
                strategy_used="text_layer",
                elements=elements,
                full_page_image=None, 
                width=w, height=h, dpi=dpi
            )
