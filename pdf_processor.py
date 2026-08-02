"""
pdf_processor.py
----------------
Handles extraction of raw text (and basic table text) from uploaded
EOB / ERA / ERN PDF files using pdfplumber, with an automatic OCR
fallback (pytesseract + pdf2image) for scanned/image-only pages.

Many real-world EOBs are exported as flat images (e.g. scanned or
screenshot-based PDFs), so pages with no extractable text layer are
rasterized and OCR'd automatically.
"""

import io
import pdfplumber

try:
    from pdf2image import convert_from_bytes
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class PDFExtractionError(Exception):
    """Raised when a PDF cannot be read or contains no extractable text."""
    pass


def _ocr_page(file_bytes: bytes, page_number: int) -> str:
    """OCR a single page (1-indexed) of the PDF and return its text."""
    if not OCR_AVAILABLE:
        return ""
    try:
        images = convert_from_bytes(
            file_bytes, dpi=250, first_page=page_number, last_page=page_number
        )
        if not images:
            return ""
        return pytesseract.image_to_string(images[0]) or ""
    except Exception:
        # OCR is a best-effort fallback; don't blow up the whole request
        return ""


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract all text (and any tables, flattened to text) from a PDF's bytes.
    Falls back to OCR for any page that has no native text layer (e.g.
    scanned or image-based EOB/ERA documents).

    Returns a single string with page breaks marked, suitable for feeding
    to an LLM for structured extraction.
    """
    if not file_bytes:
        raise PDFExtractionError("Uploaded file is empty.")

    pages_text = []
    ocr_used_on = []

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            if len(pdf.pages) == 0:
                raise PDFExtractionError("PDF has no pages.")

            for page_num, page in enumerate(pdf.pages, start=1):
                chunk = [f"--- Page {page_num} ---"]

                text = page.extract_text() or ""

                # Also try to pull structured tables (common in EOB/ERA docs)
                try:
                    tables = page.extract_tables()
                except Exception:
                    tables = []

                has_native_content = bool(text.strip()) or bool(tables)

                if not has_native_content:
                    # Likely a scanned/image page -> OCR fallback
                    ocr_text = _ocr_page(file_bytes, page_num)
                    if ocr_text.strip():
                        chunk.append(ocr_text)
                        ocr_used_on.append(page_num)
                else:
                    if text.strip():
                        chunk.append(text)
                    for t_idx, table in enumerate(tables, start=1):
                        chunk.append(f"[Table {t_idx} on page {page_num}]")
                        for row in table:
                            clean_row = [
                                (cell or "").strip().replace("\n", " ")
                                for cell in row
                            ]
                            chunk.append(" | ".join(clean_row))

                pages_text.append("\n".join(chunk))

    except PDFExtractionError:
        raise
    except Exception as e:
        raise PDFExtractionError(f"Failed to read PDF: {e}")

    full_text = "\n\n".join(pages_text).strip()

    if not full_text:
        if not OCR_AVAILABLE:
            raise PDFExtractionError(
                "No extractable text found in this PDF, and OCR dependencies "
                "(pytesseract/pdf2image/poppler) are not installed. See README "
                "for setup instructions."
            )
        raise PDFExtractionError(
            "No extractable text found in this PDF, even after attempting OCR. "
            "The scan quality may be too low."
        )

    if ocr_used_on:
        full_text += (
            f"\n\n[Note: OCR was used to read page(s) {ocr_used_on} of this "
            "document because they had no embedded text layer. Some values "
            "may be slightly less accurate due to OCR.]"
        )

    return full_text

