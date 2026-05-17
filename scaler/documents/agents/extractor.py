"""
Agent 2 — ContentExtractor

Responsibilities:
  - Extract text (direct or via Vision API OCR)
  - Extract tables
  - Extract images and upload them to GCS
  - Produce per-page RawExtractedContent

Session state read:   processing_plan, file_bytes, document_id
Session state written: extracted_content: {"pages": [...]}
"""
import logging

from django.conf import settings
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

from ..tools.extraction import extract_docx_content
from ..tools.extraction import extract_pdf_pages

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def extract_content(tool_context: ToolContext) -> dict:
    """
    Extract all content from the document using the ProcessingPlan.

    Reads file_bytes, document_id, and processing_plan from state.
    Writes extracted_content to state.
    Returns summary stats.
    """
    file_bytes: bytes = tool_context.state["file_bytes"]
    document_id: str = tool_context.state["document_id"]
    plan: dict = tool_context.state.get("processing_plan", {})

    file_format = plan.get("file_format", "unknown")
    requires_ocr = plan.get("requires_ocr", False)

    if file_format == "pdf":
        pages = extract_pdf_pages(file_bytes, document_id, is_scanned=requires_ocr)
        extracted = {"pages": pages}
    elif file_format == "docx":
        extracted = extract_docx_content(file_bytes, document_id)
    else:
        msg = f"Unsupported file format: {file_format}"
        raise ValueError(msg)

    tool_context.state["extracted_content"] = extracted

    total_images = sum(len(p.get("images", [])) for p in extracted["pages"])
    total_tables = sum(len(p.get("tables", [])) for p in extracted["pages"])
    total_chars = sum(len(p.get("text", "")) for p in extracted["pages"])

    summary = {
        "page_count": len(extracted["pages"]),
        "total_images": total_images,
        "total_tables": total_tables,
        "total_chars": total_chars,
    }
    logger.info("Extraction complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

content_extractor = LlmAgent(
    name="content_extractor",
    model=LiteLlm(
        model=f"mistral/{settings.MISTRAL_FLASH_MODEL}",
        max_tokens=settings.LLM_MAX_TOKENS,
    ),
    tools=[extract_content],
    output_key="extractor_output",
    instruction="""
You are a content extractor. Your sole job is to extract all content from the
document using the pre-built ProcessingPlan.

Steps:
1. Call extract_content() — this handles text (OCR or direct), tables, and
   images automatically based on the ProcessingPlan in session state.
   Images are uploaded to GCS and their URIs are stored in the extracted content.

Return a single line summarising the extraction result, e.g.:
"Extracted 12 pages, 3 tables, 7 images, 24500 characters."
""",
)
