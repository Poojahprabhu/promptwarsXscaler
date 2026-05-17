"""
Text, table, and image extraction for PDF and DOCX files.

Extraction strategy:
  PDF (text-based): PyMuPDF for text, pdfplumber for tables, PyMuPDF for images
  PDF (scanned):    Vision API OCR for text, Gemini Vision for tables, PyMuPDF for images
  DOCX:             python-docx for text + tables, python-docx for embedded images
"""
import io
import logging
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber
from docx import Document as DocxDocument
from docx.oxml.ns import qn

from .gcs import build_image_gcs_path
from .gcs import upload_bytes_to_gcs
from .vision import ocr_page_bytes

logger = logging.getLogger(__name__)

SIGNATURE_KEYWORDS = frozenset(["signature", "signed", "sign here", "authorized signature"])


def _page_has_signature(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in SIGNATURE_KEYWORDS)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def is_pdf_scanned(file_bytes: bytes) -> bool:
    """Return True if the PDF has no selectable text (i.e. it is scan-based)."""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            if page.get_text("text").strip():
                return False
    return True


def extract_pdf_pages(file_bytes: bytes, document_id: str, is_scanned: bool) -> list[dict]:
    """
    Extract per-page content from a PDF.

    Returns a list of page dicts:
      {page, text, tables, images: [gcs_uri, ...], has_signature}
    """
    pages = []

    with fitz.open(stream=file_bytes, filetype="pdf") as fitz_doc:
        with pdfplumber.open(io.BytesIO(file_bytes)) as plumber_doc:
            for page_num, fitz_page in enumerate(fitz_doc, start=1):
                page_data: dict = {"page": page_num, "text": "", "tables": [], "images": [], "has_signature": False}

                # --- Text ---
                if is_scanned:
                    img_bytes = fitz_page.get_pixmap(dpi=200).tobytes("png")
                    page_data["text"] = ocr_page_bytes(img_bytes)
                else:
                    page_data["text"] = fitz_page.get_text("text")

                page_data["has_signature"] = _page_has_signature(page_data["text"])

                # --- Tables (native PDFs only; scanned tables handled by Gemini Vision) ---
                if not is_scanned and page_num <= len(plumber_doc.pages):
                    plumber_page = plumber_doc.pages[page_num - 1]
                    for table in plumber_page.extract_tables():
                        if table:
                            page_data["tables"].append(table)

                # --- Images ---
                for img_index, img_info in enumerate(fitz_page.get_images(full=True)):
                    xref = img_info[0]
                    try:
                        base_image = fitz_doc.extract_image(xref)
                        img_bytes = base_image["image"]
                        ext = base_image.get("ext", "png")
                        gcs_path = build_image_gcs_path(document_id, page_num, img_index, ext)
                        gcs_uri = upload_bytes_to_gcs(img_bytes, gcs_path, f"image/{ext}")
                        page_data["images"].append(gcs_uri)
                    except Exception:
                        logger.exception("Failed to extract image xref=%d on page %d", xref, page_num)

                pages.append(page_data)

    logger.info("Extracted %d pages from PDF (scanned=%s)", len(pages), is_scanned)
    return pages


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def extract_docx_content(file_bytes: bytes, document_id: str) -> dict:
    """
    Extract text, tables, and embedded images from a DOCX file.

    Returns:
      {pages: [{"page": 1, "text": "...", "tables": [...], "images": [...], "has_signature": False}]}

    DOCX files don't have hard page boundaries, so we return a single "page 1" entry
    containing all content. Page refs will be 1-indexed paragraph numbers instead.
    """
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        doc = DocxDocument(str(tmp_path))

        # Full text
        full_text = "\n".join(para.text for para in doc.paragraphs if para.text.strip())

        # Tables
        tables = []
        for table in doc.tables:
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            tables.append(rows)

        # Embedded images (stored in the docx zip as media/*)
        images = []
        for rel in doc.part.rels.values():
            if "image" in rel.reltype:
                try:
                    img_bytes = rel.target_part.blob
                    ext = rel.target_ref.rsplit(".", 1)[-1] if "." in rel.target_ref else "png"
                    gcs_path = build_image_gcs_path(document_id, 1, len(images), ext)
                    gcs_uri = upload_bytes_to_gcs(img_bytes, gcs_path, f"image/{ext}")
                    images.append(gcs_uri)
                except Exception:
                    logger.exception("Failed to extract DOCX image from rel %s", rel.target_ref)

        page_entry = {
            "page": 1,
            "text": full_text,
            "tables": tables,
            "images": images,
            "has_signature": _page_has_signature(full_text),
        }

        logger.info(
            "Extracted DOCX: %d chars, %d tables, %d images",
            len(full_text),
            len(tables),
            len(images),
        )
        return {"pages": [page_entry]}

    finally:
        tmp_path.unlink(missing_ok=True)
