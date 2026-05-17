"""
Agent 1 — DocumentProfiler

Responsibilities:
  - Detect file format (pdf / docx)
  - Determine whether a PDF is scan-based or text-based
  - Produce a preliminary document type classification
  - Write a ProcessingPlan to session state

Session state written:
  processing_plan: {
      file_format,
      is_scanned,       # PDF only
      preliminary_doc_type,
      requires_ocr,
      page_count,
  }
"""
import logging

import fitz  # PyMuPDF
from django.conf import settings
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

from ..tools.extraction import is_pdf_scanned

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def detect_format(tool_context: ToolContext) -> dict:
    """Detect the file format and basic metadata from the document bytes stored in state."""
    file_bytes: bytes = tool_context.state["file_bytes"]
    original_filename: str = tool_context.state.get("original_filename", "")

    name_lower = original_filename.lower()
    if name_lower.endswith(".pdf"):
        file_format = "pdf"
    elif name_lower.endswith(".docx"):
        file_format = "docx"
    else:
        # Fall back to magic-byte sniffing
        if file_bytes[:4] == b"%PDF":
            file_format = "pdf"
        elif file_bytes[:2] == b"PK":
            file_format = "docx"
        else:
            file_format = "unknown"

    page_count = 0
    if file_format == "pdf":
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            page_count = doc.page_count

    result = {"file_format": file_format, "page_count": page_count}
    tool_context.state["detected_format"] = result
    logger.info("Detected format: %s, pages: %d", file_format, page_count)
    return result


def check_if_scanned(tool_context: ToolContext) -> dict:
    """Check whether the PDF requires OCR. Returns {"requires_ocr": bool}."""
    file_bytes: bytes = tool_context.state["file_bytes"]
    detected = tool_context.state.get("detected_format", {})

    requires_ocr = False
    if detected.get("file_format") == "pdf":
        requires_ocr = is_pdf_scanned(file_bytes)

    result = {"requires_ocr": requires_ocr}
    tool_context.state["scan_check"] = result
    logger.info("Scan check: requires_ocr=%s", requires_ocr)
    return result


def build_processing_plan(preliminary_doc_type: str, tool_context: ToolContext) -> dict:
    """
    Assemble the final ProcessingPlan and persist it to session state.

    Args:
        preliminary_doc_type: The document type as classified by the LLM
                              (e.g. employment_contract, invoice, research_paper).
    """
    detected = tool_context.state.get("detected_format", {})
    scan_check = tool_context.state.get("scan_check", {})

    plan = {
        "file_format": detected.get("file_format", "unknown"),
        "page_count": detected.get("page_count", 0),
        "requires_ocr": scan_check.get("requires_ocr", False),
        "preliminary_doc_type": preliminary_doc_type,
    }
    tool_context.state["processing_plan"] = plan
    logger.info("ProcessingPlan: %s", plan)
    return plan


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

document_profiler = LlmAgent(
    name="document_profiler",
    model=LiteLlm(
        model=f"mistral/{settings.MISTRAL_FLASH_MODEL}",
        max_tokens=settings.LLM_MAX_TOKENS,
    ),
    tools=[detect_format, check_if_scanned, build_processing_plan],
    output_key="profiler_output",
    instruction="""
You are a document profiler. Your job is to analyze an uploaded document and
produce a structured processing plan.

Follow these steps IN ORDER:
1. Call detect_format() to identify the file format and page count.
2. Call check_if_scanned() to determine if OCR is required (PDFs only).
3. Based on the detected format and any text visible in the filename or first-page
   clues stored in state, classify the document into ONE of these types:
     employment_contract | service_agreement | nda | invoice | purchase_order |
     research_paper | technical_manual | financial_report | legal_brief | other
4. Call build_processing_plan(preliminary_doc_type=<your classification>) to
   persist the plan.

Return a single line confirming the plan was built, e.g.:
"ProcessingPlan created: pdf, 12 pages, requires_ocr=false, type=employment_contract"
""",
)
