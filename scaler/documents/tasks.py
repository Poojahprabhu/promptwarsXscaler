import asyncio
import logging
import uuid

from celery import shared_task
from django.utils import timezone

from .models import Document
from .models import DocumentChunk
from .models import DocumentStatus
from .models import FinancialRisk
from .models import GlossaryEntry
from .models import RiskFinding
from .models import RiskSeverity

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    time_limit=30 * 60,
    soft_time_limit=25 * 60,
)
def run_document_pipeline(self, document_id: str) -> dict:
    """
    Celery entry point for the document processing pipeline.

    Downloads the document from GCS, runs the 3-agent ADK pipeline,
    and persists the resulting chunks to PostgreSQL.
    """
    # Lazy import to avoid circular dependencies
    from .agents.pipeline import run_document_pipeline as _run_pipeline  # noqa: PLC0415

    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document %s not found — aborting task", document_id)
        return {"status": "error", "detail": "Document not found"}

    document.status = DocumentStatus.PROCESSING
    document.celery_task_id = self.request.id or ""
    document.save(update_fields=["status", "celery_task_id"])
    logger.info("Started pipeline for document %s (%s)", document_id, document.original_filename)

    try:
        file_bytes = document.file.read()

        state = asyncio.run(
            _run_pipeline(
                file_bytes=file_bytes,
                document_id=document_id,
                user_id=str(document.user_id),
                original_filename=document.original_filename,
                include_external_references=document.include_external_references,
            )
        )

        _persist_chunks(document, state)
        _persist_risk_findings(document, state)
        _persist_financial_risks(document, state)
        _persist_glossary_entries(document, state)

        document.status = DocumentStatus.COMPLETED
        document.completed_at = timezone.now()
        document.document_type = state.get("document_type", "")
        document.chunking_strategy = state.get("chunking_strategy", "")
        document.save(update_fields=["status", "completed_at", "document_type", "chunking_strategy"])

        chunk_count = len(state.get("final_chunks", []))
        logger.info("Pipeline completed for document %s: %d chunks", document_id, chunk_count)
        return {"status": "completed", "chunks": chunk_count}

    except Exception as exc:
        logger.exception("Pipeline failed for document %s", document_id)
        document.status = DocumentStatus.FAILED
        document.error_message = str(exc)
        document.save(update_fields=["status", "error_message"])
        raise self.retry(exc=exc)


def _persist_chunks(document: Document, state: dict) -> None:
    """Save final_chunks from pipeline state to the DocumentChunk table."""
    final_chunks: list[dict] = state.get("final_chunks", [])
    if not final_chunks:
        logger.warning("No chunks to persist for document %s", document.id)
        return

    chunks_to_create = [
        DocumentChunk(
            id=uuid.uuid4(),
            document=document,
            chunk_id=chunk.get("chunk_id", ""),
            section=chunk.get("section", ""),
            text=chunk.get("text", ""),
            page_refs=chunk.get("page_refs", []),
            images=chunk.get("images", []),
            has_table=chunk.get("has_table", False),
            has_signature=chunk.get("has_signature", False),
            chunk_metadata=chunk.get("metadata", {}),
            qdrant_point_id=chunk.get("qdrant_point_id"),
        )
        for chunk in final_chunks
    ]
    DocumentChunk.objects.bulk_create(chunks_to_create)
    logger.info("Persisted %d chunks for document %s", len(chunks_to_create), document.id)


_VALID_SEVERITIES = {choice.value for choice in RiskSeverity}


def _persist_risk_findings(document: Document, state: dict) -> None:
    """Save risk_findings from pipeline state to the RiskFinding table."""
    findings: list[dict] = state.get("risk_findings", [])
    if not findings:
        logger.warning("No risk findings to persist for document %s", document.id)
        return

    records = []
    for f in findings:
        severity = f.get("severity") if f.get("severity") in _VALID_SEVERITIES else RiskSeverity.MEDIUM
        ambiguous_terms = f.get("ambiguous_terms") or []
        if not isinstance(ambiguous_terms, list):
            ambiguous_terms = []
        records.append(
            RiskFinding(
                id=uuid.uuid4(),
                document=document,
                clause_id=str(f.get("clause_id", ""))[:255],
                risk_type=str(f.get("risk_type", "unspecified"))[:255],
                severity=severity,
                confidence=max(0.0, min(1.0, float(f.get("confidence", 0.5) or 0.5))),
                raw_explanation=f.get("raw_explanation", "") or "",
                action=f.get("action", "") or "",
                ambiguous_terms=ambiguous_terms,
                risk_of_interpretation=f.get("risk_of_interpretation", "") or "",
            )
        )

    RiskFinding.objects.bulk_create(records)
    logger.info("Persisted %d risk findings for document %s", len(records), document.id)


def _persist_financial_risks(document: Document, state: dict) -> None:
    """Save financial_risks from pipeline state to the FinancialRisk table."""
    risks: list[dict] = state.get("financial_risks", [])
    if not risks:
        logger.info("No financial risks reported for document %s", document.id)
        return

    records = [
        FinancialRisk(
            id=uuid.uuid4(),
            document=document,
            clause_id=str(r.get("clause_id", ""))[:255],
            financial_exposure=r.get("financial_exposure", "") or "",
            conditions=r.get("conditions", "") or "",
            worst_case_cost=r.get("worst_case_cost", "") or "",
        )
        for r in risks
    ]
    FinancialRisk.objects.bulk_create(records)
    logger.info("Persisted %d financial risks for document %s", len(records), document.id)


def _persist_glossary_entries(document: Document, state: dict) -> None:
    """Save glossary_entries from pipeline state to the GlossaryEntry table."""
    entries: list[dict] = state.get("glossary_entries", [])
    if not entries:
        logger.info("No glossary entries reported for document %s", document.id)
        return

    # Defense in depth — the agent already drops external refs when the user
    # disabled them, but if the flag changed mid-run we re-enforce here.
    include_external = document.include_external_references

    records = []
    for entry in entries:
        external_refs = entry.get("external_references") or []
        if not include_external or not isinstance(external_refs, list):
            external_refs = []

        clause_ids = entry.get("clause_ids") or []
        if not isinstance(clause_ids, list):
            clause_ids = []

        records.append(
            GlossaryEntry(
                id=uuid.uuid4(),
                document=document,
                term=str(entry.get("term", ""))[:255],
                definition=entry.get("definition", "") or "",
                context=entry.get("context", "") or "",
                clause_ids=[str(c)[:255] for c in clause_ids],
                external_references=external_refs,
            )
        )

    # ignore_conflicts protects against the per-document unique(term) constraint
    # in the unlikely event the agent emits duplicates after dedup.
    GlossaryEntry.objects.bulk_create(records, ignore_conflicts=True)
    logger.info(
        "Persisted %d glossary entries for document %s (external_refs=%s)",
        len(records), document.id, include_external,
    )
