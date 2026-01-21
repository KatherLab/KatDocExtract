# KatDocExtract (Mistral-compatible Document OCR Wrapper using DeepSeek-OCR)

A **Mistral-API compatible** document parsing server that converts PDFs, images, and Office documents into clean, formatted **Markdown** using **DeepSeek-OCR**.

This project is designed as a *drop-in OCR replacement* for LLM pipelines (RAG, chatbots, Open WebUI, etc.) that need high-quality extraction from complex documents.

> **Important:** This repository does **not** ship the model runtime. You must run your **own vLLM instance** serving `deepseek-ai/DeepSeek-OCR` (OpenAI-compatible API).  
> Follow the vLLM recipe here: `https://docs.vllm.ai/projects/recipes/en/latest/DeepSeek/DeepSeek-OCR.html`

---

## Features

- **Mistral Compatibility**  
  The API structure mirrors Mistral OCR-style endpoints, making integration with existing tools easy.

- **Smart Hybrid PDF Parsing**
  - **Digital PDFs:** Extracts text from the PDF text layer (fast & accurate), while using Vision AI only for figures/charts.
  - **Scanned PDFs / broken encoding:** Automatically detects scanned pages or bad encoding (“mojibake”) and switches to full Vision OCR.

- **Office Support (`.docx`, `.pptx`, …)**  
  Converts Office docs to PDF via **LibreOffice**, then processes the resulting PDF.

- **High Performance (Async Map-Reduce)**  
  Pages and figure crops are processed concurrently, bounded by a configurable vLLM concurrency limit.

- **Figure Understanding**
  Detects charts/diagrams, crops them, and asks the Vision model to describe them, inserting the description into the Markdown flow.

---

## How it works (Architecture)

1. You run **vLLM** with `deepseek-ai/DeepSeek-OCR` (OpenAI-compatible `/v1` endpoint).
2. This server accepts documents and:
   - For PDFs: decides per page whether to use text-layer extraction or Vision OCR fallback.
   - For figures: crops and re-queries the model for figure descriptions (optional).
3. Returns a structured response with Markdown pages (and optional image/table objects).

---

## Prerequisites

- Python 3.13+ recommended
- `uv` (recommended) or pip/venv
- A running **vLLM** server hosting `deepseek-ai/DeepSeek-OCR` (GPU strongly recommended)
- Optional but recommended:
  - **LibreOffice** (`soffice`) for Office docs (`.doc/.docx/.ppt/.pptx/.odt/.odp`)
- System libs:
  - Uses **PyMuPDF** (`fitz`) for PDF rendering

---

## 1) Run your own vLLM backend (required)

This wrapper expects an OpenAI-compatible server at `VLLM_BASE_URL` (defaults to `http://localhost:8000/v1`).

### Install vLLM

```bash
uv venv
source .venv/bin/activate
uv pip install -U vllm --torch-backend auto
````

### Serve DeepSeek-OCR (OpenAI-compatible)

```bash
vllm serve deepseek-ai/DeepSeek-OCR \
  --logits_processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0
```

**Why these flags?**

* The **custom logits processor** is important for best OCR/Markdown behavior.
* Prefix caching and multimodal processor caching usually don’t help OCR-style single-turn calls and may add overhead.

---

## 2) Install and run KatDocExtract

We recommend using **uv**.

### Install

```bash
uv sync
```

### Configure

Copy and edit the environment file:

```bash
cp .env.example .env
# Edit .env and set VLLM_BASE_URL (and optionally VLLM_MODEL)
```

`.env.example` (important fields)

* `VLLM_BASE_URL` – your vLLM OpenAI-compatible endpoint (e.g. `http://localhost:8000/v1`)
* `VLLM_MODEL` – model name (default `deepseek-ai/DeepSeek-OCR`)
* `CONCURRENT_REQUEST_LIMIT` – max concurrent requests to vLLM (tune for GPU memory)

### Run

```bash
uv run uvicorn app:app --host 0.0.0.0 --port 8005 --reload
```

Open the Web UI:

* `http://localhost:8005`

---

## API Usage

### Endpoint: `POST /v1/ocr`

This is the main endpoint. It accepts a `document` object plus optional parsing controls.

**Minimal example (remote PDF URL):**

```bash
curl -X POST "http://localhost:8005/v1/ocr" \
  -H "Content-Type: application/json" \
  -d '{
    "document": {
      "type": "document_url",
      "document_url": "https://example.com/report.pdf"
    },
    "pdf_mode": "auto",
    "inline_figure_text": true
  }'
```

**Minimal example (remote image URL):**

```bash
curl -X POST "http://localhost:8005/v1/ocr" \
  -H "Content-Type: application/json" \
  -d '{
    "document": {
      "type": "image_url",
      "image_url": {"url": "https://example.com/scan.png"}
    }
  }'
```

> Tip: This server also supports `data:` URLs (base64-encoded) for `document_url` and `image_url`.
> The built-in web UI uses `data:` URLs under the hood.

---

## PDF modes and heuristics

### `pdf_mode`

* `"auto"` (default): per-page decision using heuristics
* `"text"`: always use the PDF text layer (fast; fails on scans/bad encoding)
* `"ocr"`: always render pages and run Vision OCR (slower; best for scanned PDFs)

### Heuristic knobs (in `.env`)

* `MIN_TEXT_DENSITY`
  If a page’s text covers less than this fraction of the page, it is treated as scanned.
* `MAX_MOJIBAKE_RATIO`
  If too many replacement/control characters appear in the text layer, the page falls back to OCR.
* `PDF_TEXT_RENDER_DPI`
  DPI used when rendering PDF pages for OCR fallback and figure crops.

---

## Figure extraction

If figure extraction is enabled (default behavior in the UI flow), the pipeline will:

* Detect image regions (either from PDF blocks in text-layer mode, or from DeepSeek OCR RefDet tags in OCR mode)
* Crop those regions
* Optionally ask DeepSeek-OCR to **describe** them and insert the description inline in Markdown

Useful request fields:

* `inline_figure_text` (bool, default true): inline descriptions as blockquotes instead of image links
* `figure_prompt` (string): override the default prompt for figure description
* `image_min_size` (int): ignore very small crops
* `image_limit` (int): only keep the largest N images per page

---

## Office documents

Office formats are supported by converting them to PDF using **LibreOffice**.

Install LibreOffice (example for Debian/Ubuntu):

```bash
sudo apt-get update && sudo apt-get install -y libreoffice
```

If LibreOffice is missing, Office conversion requests will return an error.

---

## Performance tuning

### Concurrency

`CONCURRENT_REQUEST_LIMIT` controls how many requests this server sends to vLLM concurrently.

* If you see **GPU OOM**, reduce it (e.g. 10–30).
* If your GPU has headroom, increase it for throughput.

### DPI tradeoff

Higher `PDF_TEXT_RENDER_DPI` improves OCR quality but increases:

* rendering time
* GPU compute
* memory usage (larger images)

150 DPI is a good starting point; 200–300 may help for small text in scans.

---

## Troubleshooting

### vLLM connection errors

* Verify `VLLM_BASE_URL` points to your running vLLM server and includes `/v1`
* Confirm the model name matches what you serve (`VLLM_MODEL`)

### Timeouts on large PDFs

* Reduce `CONCURRENT_REQUEST_LIMIT`
* Consider lowering DPI
* Ensure your vLLM server timeout is high enough (this project uses a long client timeout)

### Office conversion fails

* Ensure `soffice` / LibreOffice is installed and available in `PATH`
* Check server logs for LibreOffice stderr output

---

## Development notes

* FastAPI app entrypoint: `app.py`
* PDF strategy: `PdfEngine` + `heuristics.PageAnalyzer`
* OCR + figure tasks: `pipeline.py`
* vLLM OpenAI-compatible client: `deepseek_client.py`

---

## License

TBD

