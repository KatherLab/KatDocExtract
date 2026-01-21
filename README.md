# DeepSeek-OCR API

This is a **Mistral-API compatible** document parsing server. It allows you to upload PDFs, Images, or Office documents and receive clean, formatted Markdown output using the **DeepSeek-OCR** model.

It is designed to be a "drop-in" OCR replacement for LLM pipelines (RAG, Chatbots) that need high-quality text extraction from complex documents.

## Features

*   **Mistral Compatibility:** The API structure mirrors Mistral's OCR endpoints, making it easy to integrate with existing tools (e.g. Open WebUI).
*   **Smart Hybrid Parsing:**
    *   **Digital PDFs:** Extracts text directly from the file (fast & accurate) while using Vision AI only for figures and charts.
    *   **Scanned PDFs:** Automatically detects scanned pages or bad encoding ("mojibake") and switches to full Vision OCR.
*   **Office Support:** Native support for `.docx`, `.pptx`, etc. (requires LibreOffice).
*   **High Performance:** Uses an async "Map-Reduce" pipeline. If you upload a 100-page PDF, the system processes pages in parallel, limited only by your GPU throughput.
*   **Figure Understanding:** Detects charts and diagrams, crops them, and uses the Vision Model to describe them in text, inserting the description into the Markdown flow.

## Setup

We recommend using [uv](https://github.com/astral-sh/uv) for fast setup.

1.  **Install uv** (if needed):
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

2.  **Install Dependencies:**
    ```bash
    uv sync
    ```

3.  **Configuration:**
    Copy the example config and edit it to point to your vLLM instance.
    ```bash
    cp .env.example .env
    # Edit .env and set VLLM_BASE_URL
    ```

4.  **Run Server:**
    ```bash
    uv run uvicorn app:app --host 0.0.0.0 --port 8005 --reload
    ```

## Usage

You can use the included Web UI at `http://localhost:8005`, or call the API directly:

**cURL Example:**
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