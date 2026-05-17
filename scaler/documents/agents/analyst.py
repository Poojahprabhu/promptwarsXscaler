"""
Agent 3 — AnalystChunker

Responsibilities:
  - Confirm document type from full extracted text
  - Select chunking strategy based on document type
  - Identify sections / clauses
  - Produce semantic chunks with full metadata
  - Generate embeddings and upsert to Qdrant

Session state read:   extracted_content, processing_plan, document_id, user_id
Session state written: final_chunks: [...]
"""
import logging

from django.conf import settings
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

from ..tools.qdrant import embed_texts
from ..tools.qdrant import upsert_chunks

logger = logging.getLogger(__name__)


CHUNKING_STRATEGIES: dict[str, str] = {
    "employment_contract": "clause_based",
    "service_agreement": "clause_based",
    "nda": "clause_based",
    "legal_brief": "clause_based",
    "invoice": "entity_based",
    "purchase_order": "entity_based",
    "research_paper": "section_based",
    "technical_manual": "heading_hierarchy",
    "financial_report": "section_based",
    "other": "paragraph_based",
}


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def create_chunks_and_store(
    document_type: str,
    sections: list[dict],
    tool_context: ToolContext,
) -> dict:
    """
    Build semantic chunks from identified sections and store them in Qdrant.

    Args:
        document_type: Confirmed document type string.
        sections: List of section objects with fields:
            chunk_id (str), section (str), text (str), page_refs (list[int]),
            has_table (bool), has_signature (bool), metadata (dict, optional).

    Returns summary of chunks created.
    """
    extracted: dict = tool_context.state["extracted_content"]
    document_id: str = tool_context.state["document_id"]
    user_id: str = tool_context.state["user_id"]
    plan: dict = tool_context.state.get("processing_plan", {})

    chunking_strategy = CHUNKING_STRATEGIES.get(document_type, "paragraph_based")

    if not isinstance(sections, list):
        raise ValueError(f"sections must be a list, got {type(sections).__name__}")

    # Build a GCS-image lookup: page_num → list of GCS URIs
    page_images: dict[int, list[str]] = {}
    for page in extracted.get("pages", []):
        page_images[page["page"]] = page.get("images", [])

    # Attach GCS image URIs to each chunk based on its page_refs
    chunks = []
    for section in sections:
        page_refs = section.get("page_refs", [])
        images = []
        for page_num in page_refs:
            images.extend(page_images.get(page_num, []))

        chunk = {
            "chunk_id": section.get("chunk_id", ""),
            "section": section.get("section", ""),
            "text": section.get("text", ""),
            "page_refs": page_refs,
            "images": images,
            "has_table": section.get("has_table", False),
            "has_signature": section.get("has_signature", False),
            "document_type": document_type,
            "chunking_strategy": chunking_strategy,
            "metadata": section.get("metadata", {}),
        }
        chunks.append(chunk)

    if not chunks:
        logger.warning("No chunks produced for document %s", document_id)
        tool_context.state["final_chunks"] = []
        return {"chunks_created": 0, "document_type": document_type, "chunking_strategy": chunking_strategy}

    texts = [chunk["text"] for chunk in chunks]
    embeddings = embed_texts(texts)

    # Upsert to Qdrant
    point_ids = upsert_chunks(chunks, embeddings, document_id, user_id)

    # Attach Qdrant IDs and write final chunks to state
    for chunk, point_id in zip(chunks, point_ids, strict=True):
        chunk["qdrant_point_id"] = point_id

    tool_context.state["final_chunks"] = chunks
    tool_context.state["document_type"] = document_type
    tool_context.state["chunking_strategy"] = chunking_strategy

    summary = {
        "chunks_created": len(chunks),
        "document_type": document_type,
        "chunking_strategy": chunking_strategy,
    }
    logger.info("Analyst complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

analyst_chunker = LlmAgent(
    name="analyst_chunker",
    model=LiteLlm(
        model=f"mistral/{settings.MISTRAL_FLASH_MODEL}",
        max_tokens=settings.LLM_MAX_TOKENS,
    ),
    tools=[create_chunks_and_store],
    output_key="analyst_output",
    instruction="""
You are a document analyst and semantic chunker.

Your input is the full extracted text across all pages in session state
(key: extracted_content). Use it to:

1. CONFIRM the document type. The preliminary type is in processing_plan.
   Re-confirm or correct it based on the full content.
   Valid types: employment_contract | service_agreement | nda | invoice |
   purchase_order | research_paper | technical_manual | financial_report |
   legal_brief | other

2. IDENTIFY the document's sections/clauses. For each section produce an
   object with these fields:
     - chunk_id: a unique slug, e.g. "clause_4_2" or "section_introduction"
     - section: human-readable section name, e.g. "Termination"
     - text: the full text of this section
     - page_refs: list of page numbers this section spans (integers)
     - has_table: true if this section contains a table
     - has_signature: true if this section has a signature block
     - metadata: any extra structured fields (clause number, amounts, dates, etc.)

3. Call create_chunks_and_store(document_type=<confirmed type>,
   sections=<list of section objects>) to embed and store the chunks. Pass
   `sections` as a structured array argument (the tool-call framework will
   serialize it); do NOT pre-encode it as a JSON string.

Return a one-line summary, e.g.:
"Analysis complete: employment_contract, clause_based, 14 chunks stored."
""",
)
