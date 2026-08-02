"""
main.py
-------
FastAPI backend for the EOB/ERA Payment Posting Extractor.

Run with:
    uvicorn main:app --reload --port 8000
"""

import os

from dotenv import load_dotenv

load_dotenv()  # load ANTHROPIC_API_KEY etc. from .env

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pdf_processor import extract_text_from_pdf, PDFExtractionError
from ai_processor import extract_payment_data, AIExtractionError

app = FastAPI(title="EOB/ERA Payment Posting Extractor")

# Allow the frontend (served separately or via file://) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE_MB = 20


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    file_bytes = await file.read()

    if len(file_bytes) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size is {MAX_FILE_SIZE_MB}MB.",
        )

    # Step 1: extract raw text from the PDF
    try:
        document_text = extract_text_from_pdf(file_bytes)
    except PDFExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Step 2: send text to Claude for structured extraction
    try:
        rows = extract_payment_data(document_text)
    except AIExtractionError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {
        "filename": file.filename,
        "row_count": len(rows),
        "rows": rows,
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "api_key_configured": bool(os.environ.get("GROQ_API_KEY"))}


# --- Serve the frontend as static files, so you can run one server locally for everything.
# (When deployed separately, e.g. frontend on Vercel + backend on Render, this mount is
# simply unused — the frontend is served by Vercel instead.)
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

if os.path.isdir(FRONTEND_DIR):
    # Mounted last (after the /api/* routes above) so API routes take priority.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
