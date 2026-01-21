from __future__ import annotations

import base64
import io
import asyncio
from typing import Optional

from openai import AsyncOpenAI
from PIL import Image

from config import get_settings

settings = get_settings()

PROMPT_PAGE = "<|grounding|>Convert the document to markdown."

# Global Semaphore to limit concurrent vLLM requests
_sem = asyncio.Semaphore(settings.CONCURRENT_REQUEST_LIMIT)

client = AsyncOpenAI(
    api_key=settings.VLLM_API_KEY,
    base_url=settings.VLLM_BASE_URL,
    timeout=3600,
)

def pil_image_to_data_url(image: Image.Image, mime: str = "image/png") -> str:
    buf = io.BytesIO()
    # Ensure we don't send CMYK or weird modes to API
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
        
    fmt = "PNG" if mime.lower() not in ("image/jpeg", "image/jpg") else "JPEG"
    image.save(buf, format=fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{b64}"

async def run_deepseek_ocr(image: Image.Image, prompt: Optional[str], model_name: str) -> str:
    """
    Async wrapper for OCR requests with concurrency limiting.
    """
    text_prompt = (prompt or "").strip() or PROMPT_PAGE
    # Offload image encoding to thread to avoid blocking event loop
    data_url = await asyncio.to_thread(pil_image_to_data_url, image, "image/png")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": text_prompt},
            ],
        }
    ]

    async with _sem:
        resp = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=8000,
            temperature=0.0,
            extra_body={
                "skip_special_tokens": False,
                "vllm_xargs": {
                    "ngram_size": 30,
                    "window_size": 90,
                    "whitelist_token_ids": [128821, 128822],
                },
            },
        )
    return resp.choices[0].message.content or ""

async def run_deepseek_text(model_name: str, text: str, prompt: str) -> str:
    messages = [{"role": "user", "content": f"{prompt}\n\n---\n\n{text}"}]
    
    async with _sem:
        resp = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=4096,
            temperature=0.0,
        )
    return resp.choices[0].message.content or ""
