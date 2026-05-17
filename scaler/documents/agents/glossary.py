"""
Agent — Glossary & External References

Responsibilities:
  - Scan every clause for jargon, legal terms-of-art, and otherwise complex vocabulary
    that a non-specialist reader would not understand.
  - Produce a plain-English definition for each term.
  - Optionally suggest one or two reputable external references (Wikipedia, Cornell LII,
    Investopedia, official .gov sources, etc.) that explain the term in more depth.
    This is gated by the user preference ``include_external_references`` stored in
    session state — when disabled the agent must leave ``external_references`` empty.

Session state read:
  - final_chunks
  - include_external_references (bool, defaults to True)

Session state written:
  - glossary_entries: [{
        term, definition, context, clause_ids: [...],
        external_references: [{title, url, source}]
    }]
"""
import json
import logging

from django.conf import settings
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# Reputable, freely accessible reference domains the agent is allowed to cite.
# Keeping this allow-list small reduces hallucinated-URL risk and keeps the user
# experience predictable. Anything outside this set is dropped at validation.
_ALLOWED_REFERENCE_DOMAINS = (
    "en.wikipedia.org",
    "www.law.cornell.edu",       # Cornell Legal Information Institute
    "www.investopedia.com",
    "www.uscourts.gov",
    "www.sec.gov",
    "www.ftc.gov",
    "www.consumerfinance.gov",
    "www.dol.gov",
    "www.merriam-webster.com",
)

_MAX_REFERENCES_PER_TERM = 2


def get_document_chunks(tool_context: ToolContext) -> dict:
    """Retrieve all document chunks the glossary agent should scan."""
    final_chunks: list[dict] = tool_context.state.get("final_chunks", [])
    return {
        "chunk_count": len(final_chunks),
        "chunks": [
            {
                "chunk_id": c.get("chunk_id"),
                "section": c.get("section"),
                "text": c.get("text"),
            }
            for c in final_chunks
        ],
    }


def get_user_preferences(tool_context: ToolContext) -> dict:
    """
    Return user-controlled flags that influence this agent's behavior.

    The pipeline seeds ``include_external_references`` into session state from the
    Document record at start-of-run. If absent we default to True.
    """
    include_refs = tool_context.state.get("include_external_references", True)
    return {"include_external_references": bool(include_refs)}


def _sanitize_references(refs, include_external: bool) -> list[dict]:
    """Drop malformed entries and entries pointing outside the allow-list."""
    if not include_external or not isinstance(refs, list):
        return []

    cleaned: list[dict] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        title = (ref.get("title") or "").strip()
        url = (ref.get("url") or "").strip()
        source = (ref.get("source") or "").strip().lower()
        if not title or not url:
            continue
        # Allow only http(s) URLs to a known domain — protects against
        # ``javascript:``/``data:`` schemes and prevents the LLM from
        # inventing arbitrary referral links.
        if not (url.startswith("https://") or url.startswith("http://")):
            continue
        if not any(domain in url for domain in _ALLOWED_REFERENCE_DOMAINS):
            continue
        cleaned.append({
            "title": title[:255],
            "url": url[:500],
            "source": source[:50],
        })
        if len(cleaned) >= _MAX_REFERENCES_PER_TERM:
            break
    return cleaned


def store_glossary_entries(
    glossary_entries_json: str,
    tool_context: ToolContext,
) -> dict:
    """
    Persist glossary entries to session state.

    Args:
        glossary_entries_json: JSON array of glossary entry objects:
            [{
              "term": "<word or short phrase>",
              "definition": "<plain-English definition, 1-3 sentences>",
              "context": "<how the term is used in this specific document>",
              "clause_ids": ["<chunk_id>", ...],
              "external_references": [
                  {"title": "...", "url": "https://...", "source": "wikipedia"}
              ]
            }, ...]

    External references are silently dropped when the user has disabled them
    or when they fail the URL allow-list check.
    """
    try:
        entries: list[dict] = json.loads(glossary_entries_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"glossary_entries_json is not valid JSON: {exc}") from exc

    include_external = bool(tool_context.state.get("include_external_references", True))

    cleaned: list[dict] = []
    seen_terms: set[str] = set()
    for entry in entries:
        term = (entry.get("term") or "").strip()
        definition = (entry.get("definition") or "").strip()
        if not term or not definition:
            continue
        # Deduplicate case-insensitively within the document.
        term_key = term.lower()
        if term_key in seen_terms:
            continue
        seen_terms.add(term_key)

        clause_ids = entry.get("clause_ids") or []
        if not isinstance(clause_ids, list):
            clause_ids = []

        cleaned.append({
            "term": term[:255],
            "definition": definition,
            "context": (entry.get("context") or "").strip(),
            "clause_ids": [str(cid)[:255] for cid in clause_ids],
            "external_references": _sanitize_references(
                entry.get("external_references"), include_external,
            ),
        })

    tool_context.state["glossary_entries"] = cleaned
    logger.info(
        "Glossary entries stored: %d entries (external_refs=%s)",
        len(cleaned), include_external,
    )
    return {"entries_stored": len(cleaned), "external_references_enabled": include_external}


_INSTRUCTION = """
You are a glossary author for contract readers. Your job is to make the document
understandable to a non-lawyer by defining every term that a typical reader would
NOT immediately understand.

STEP 1: Call get_user_preferences() and remember the include_external_references flag.

STEP 2: Call get_document_chunks() to retrieve all clauses.

STEP 3: Scan every clause and collect a deduplicated list of terms that need a
glossary entry. INCLUDE:
  - Legal terms of art (e.g. "indemnification", "force majeure", "liquidated damages",
    "tortious", "consequential damages", "novation", "estoppel")
  - Latin phrases ("pro rata", "ipso facto", "ex parte", "bona fide")
  - Domain-specific jargon (industry-specific, technical, or regulatory)
  - Defined terms used in this contract that a reader may forget the meaning of
  - Acronyms on first occurrence

EXCLUDE:
  - Common English words a 12th-grade reader would know
  - Proper nouns (party names, place names)
  - Numbers, dates, and monetary amounts

STEP 4: For each term, write a glossary entry of the form:
{
  "term": "<word or phrase, exactly as it appears, normalized to lowercase unless a proper noun>",
  "definition": "<1-3 plain-English sentences. No legalese. Assume a smart non-lawyer.>",
  "context": "<one sentence on how the term functions in THIS document specifically>",
  "clause_ids": ["<chunk_id where the term appears>", ...],
  "external_references": [ ... see Step 5 ... ]
}

STEP 5: External references — ONLY if include_external_references is true.
If the flag is false, set "external_references": [] for every entry and do not
fabricate links.

If the flag is true, you MAY include up to 2 references per term, choosing from
these reputable sources only:
  - en.wikipedia.org
  - www.law.cornell.edu          (Cornell LII — best for US legal terms)
  - www.investopedia.com         (best for financial terms)
  - www.merriam-webster.com
  - .gov sources: uscourts.gov, sec.gov, ftc.gov, consumerfinance.gov, dol.gov

Rules for references:
  - Only suggest a reference if you are confident the URL exists and the page
    actually explains this term. If unsure, omit it — an empty list is better
    than a broken link.
  - Format: {"title": "<page title>", "url": "<full https URL>", "source": "<wikipedia|cornell_lii|investopedia|merriam_webster|gov>"}
  - Do NOT cite blog posts, law firm marketing pages, paywalled sites, or any
    domain not in the list above. They will be filtered out.

STEP 6: Call store_glossary_entries(glossary_entries_json=<JSON array of all entries>).

Return a one-line summary, e.g.:
"Glossary complete: 14 terms defined (external references: enabled)."
or
"Glossary complete: 14 terms defined (external references: disabled by user)."
"""

glossary_agent = LlmAgent(
    name="glossary",
    model=LiteLlm(
        model=f"mistral/{settings.MISTRAL_FLASH_MODEL}",
        max_tokens=settings.LLM_MAX_TOKENS,
    ),
    tools=[get_document_chunks, get_user_preferences, store_glossary_entries],
    output_key="glossary_output",
    instruction=_INSTRUCTION,
)
