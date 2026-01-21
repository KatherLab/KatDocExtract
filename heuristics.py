from dataclasses import dataclass
import re

@dataclass
class PageAnalysis:
    has_text_layer: bool
    is_scanned: bool
    reason: str

class PageAnalyzer:
    """
    Analyzes a PDF page to determine if we should use the text layer
    or fallback to Vision OCR.
    """
    
    def __init__(self, min_density: float = 0.01, max_mojibake: float = 0.2):
        self.min_density = min_density
        self.max_mojibake = max_mojibake
        # Regex for "unknown" characters often seen in bad OCR layers (replacement chars, control codes)
        self.mojibake_re = re.compile(r"[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f]")

    def analyze(self, page_text: str, page_area_px: float, text_area_px: float) -> PageAnalysis:
        """
        Decides if a page is likely scanned based on text properties.
        """
        clean_text = page_text.strip()
        
        # 1. Empty text layer -> Scanned
        if not clean_text:
            return PageAnalysis(False, True, "No text detected in layer")

        # 2. Text Density Check
        # If text covers a tiny fraction of the page, it might be hidden text or watermarks.
        # Exception: If it has a reasonable amount of characters (e.g. > 50), it might just be a sparse page.
        if page_area_px > 0:
            density = text_area_px / page_area_px
            if density < self.min_density and len(clean_text) < 50:
                return PageAnalysis(False, True, f"Low text density ({density:.4f})")

        # 3. Mojibake / Encoding Quality Check
        # Count replacement characters or control characters
        bad_chars = len(self.mojibake_re.findall(clean_text))
        total_chars = len(clean_text)
        
        if total_chars > 0:
            ratio = bad_chars / total_chars
            if ratio > self.max_mojibake:
                return PageAnalysis(False, True, f"High mojibake ratio ({ratio:.2f})")

        return PageAnalysis(True, False, "Valid text layer detected")
