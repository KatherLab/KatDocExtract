import os
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # --- Service Config ---
    FILES_DIR: str = os.getenv("FILES_DIR", "./files_store")
    
    # --- vLLM / Model Config ---
    VLLM_BASE_URL: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
    VLLM_API_KEY: str = os.getenv("VLLM_API_KEY", "EMPTY")
    DEFAULT_MODEL_NAME: str = os.getenv("VLLM_MODEL", "deepseek-ai/DeepSeek-OCR")
    
    # --- Concurrency Control ---
    # Limit parallel requests to vLLM to prevent OOM/Timeouts
    CONCURRENT_REQUEST_LIMIT: int = int(os.getenv("CONCURRENT_REQUEST_LIMIT", "100"))

    # --- PDF Processing Defaults ---
    PDF_TEXT_RENDER_DPI: int = int(os.getenv("PDF_TEXT_RENDER_DPI", "150"))
    
    # --- Heuristics (Auto-Detection) ---
    # Minimum text density (0.0 - 1.0) to consider a page "digital"
    MIN_TEXT_DENSITY: float = float(os.getenv("MIN_TEXT_DENSITY", "0.01"))
    # Max allowed "garbage" characters ratio before falling back to OCR
    MAX_MOJIBAKE_RATIO: float = float(os.getenv("MAX_MOJIBAKE_RATIO", "0.2"))

    # --- Output Formatting ---
    # If True, separates pages with "---". If False, continuous stream.
    MARKDOWN_PAGE_BREAKS: bool = os.getenv("MARKDOWN_PAGE_BREAKS", "true").lower() in ("true", "1", "yes")

@lru_cache()
def get_settings():
    return Settings()
