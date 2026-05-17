import logging

from django.conf import settings
from django.db.models import Count
from django.db.models import Q
from django.shortcuts import get_object_or_404
from openai import OpenAI
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document
from .models import DocumentChunk
from .models import DocumentStatus
from .models import FinancialRisk
from .models import GlossaryEntry
from .models import RiskFinding
from .serializers import DocumentChunkSerializer
from .serializers import DocumentPreferencesSerializer
from .serializers import DocumentStatusSerializer
from .serializers import DocumentUploadSerializer
from .serializers import FinancialRiskSerializer
from .serializers import GlossaryEntrySerializer
from .serializers import QuerySerializer
from .serializers import RiskFindingSerializer
from .serializers import SearchSerializer
from .tasks import run_document_pipeline
from .tools.qdrant import embed_texts
from .tools.qdrant import search_chunks

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentUploadView",
    "DocumentStatusView",
    "DocumentChunksView",
    "DocumentSearchView",
    "DocumentListView",
    "DocumentAnalysisView",
    "DocumentRisksView",
    "DocumentFinancialRisksView",
    "DocumentGlossaryView",
    "DocumentPreferencesView",
    "DocumentQueryView",
]

SEVERITY_WEIGHTS = {
    "critical": 100,
    "high": 75,
    "medium": 50,
    "low": 25,
    "safe": 0,
}


def _mistral_client() -> OpenAI:
    return OpenAI(
        api_key=settings.MISTRAL_API_KEY,
        base_url=settings.MISTRAL_BASE_URL,
    )


def _document_risk_score(severities: list[str]) -> int:
    """Compute a 0–100 risk score from a list of severity strings."""
    if not severities:
        return 0
    total = sum(SEVERITY_WEIGHTS.get(s, 0) for s in severities)
    return round(total / len(severities))


class DocumentUploadView(APIView):
    """Accept a document upload, persist it, and enqueue the processing pipeline."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        original_filename = uploaded_file.name
        content_type = getattr(uploaded_file, "content_type", "")
        file_format = "pdf" if "pdf" in content_type else "docx"
        include_external_references = serializer.validated_data.get(
            "include_external_references", True,
        )

        document = Document.objects.create(
            user=request.user,
            file=uploaded_file,
            original_filename=original_filename,
            file_format=file_format,
            status=DocumentStatus.PENDING,
            include_external_references=include_external_references,
        )

        task = run_document_pipeline.delay(str(document.id))
        document.celery_task_id = task.id
        document.save(update_fields=["celery_task_id"])

        logger.info(
            "Document %s uploaded by user %s — task %s enqueued",
            document.id,
            request.user.id,
            task.id,
        )
        return Response(
            {
                "document_id": str(document.id),
                "status": document.status,
                "original_filename": document.original_filename,
                "include_external_references": document.include_external_references,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class DocumentStatusView(APIView):
    """Return the current processing status of a document."""

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)
        serializer = DocumentStatusSerializer(document)
        return Response(serializer.data)


class DocumentChunksPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class DocumentChunksView(APIView):
    """Return paginated semantic chunks for a completed document."""

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)

        if document.status != DocumentStatus.COMPLETED:
            return Response(
                {"detail": f"Document is not ready yet (status={document.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        chunks = DocumentChunk.objects.filter(document=document)
        paginator = DocumentChunksPagination()
        page = paginator.paginate_queryset(chunks, request)
        serializer = DocumentChunkSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class DocumentSearchView(APIView):
    """
    Semantic search across a user's chunks in Qdrant.

    POST body: {"query": "...", "document_id": "<uuid>", "limit": 10}
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        query = serializer.validated_data["query"]
        document_id = (
            str(serializer.validated_data["document_id"])
            if "document_id" in serializer.validated_data
            else None
        )
        limit = serializer.validated_data["limit"]

        if document_id:
            get_object_or_404(Document, id=document_id, user=request.user)

        query_vector = embed_texts([query])[0]

        results = search_chunks(
            query_vector=query_vector,
            query_text=query,
            user_id=str(request.user.id),
            document_id=document_id,
            limit=limit,
        )

        return Response({"query": query, "results": results})


class DocumentListView(APIView):
    """List the current user's documents with summary risk stats — drives the dashboard."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        documents = (
            Document.objects.filter(user=request.user)
            .annotate(
                critical_count=Count(
                    "risk_findings", filter=Q(risk_findings__severity="critical")
                ),
                high_count=Count(
                    "risk_findings", filter=Q(risk_findings__severity="high")
                ),
            )
            .order_by("-created_at")
        )

        # Pre-fetch severities per document for the score calc (single query).
        severity_map: dict[str, list[str]] = {}
        for doc_id, severity in RiskFinding.objects.filter(
            document__user=request.user
        ).values_list("document_id", "severity"):
            severity_map.setdefault(str(doc_id), []).append(severity)

        payload = []
        for doc in documents:
            severities = severity_map.get(str(doc.id), [])
            score = _document_risk_score(severities)
            payload.append(
                {
                    "id": str(doc.id),
                    "name": doc.original_filename,
                    "org": "",
                    "type": doc.document_type or "Document",
                    "status": doc.status,
                    "score": score,
                    "critical_count": doc.critical_count,
                    "high_count": doc.high_count,
                    "created_at": doc.created_at.isoformat(),
                    "completed_at": doc.completed_at.isoformat() if doc.completed_at else None,
                }
            )

        return Response(payload)


class DocumentAnalysisView(APIView):
    """
    Aggregated analysis payload for the frontend analysis page.

    Combines: document metadata + per-clause risk findings + financial risks +
    a computed overall risk score.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)

        if document.status != DocumentStatus.COMPLETED:
            return Response(
                {
                    "id": str(document.id),
                    "name": document.original_filename,
                    "type": document.document_type or "Document",
                    "status": document.status,
                    "score": 0,
                    "clauses": [],
                    "financial_risks": [],
                    "detail": f"Document is not ready yet (status={document.status}).",
                },
                status=status.HTTP_409_CONFLICT,
            )

        chunks = {c.chunk_id: c for c in document.chunks.all()}
        risks = list(document.risk_findings.all())
        financials_by_clause: dict[str, FinancialRisk] = {
            fr.clause_id: fr for fr in document.financial_risks.all()
        }

        clauses_payload = []
        for risk in risks:
            chunk = chunks.get(risk.clause_id)
            excerpt = ""
            title = risk.clause_id
            if chunk:
                title = chunk.section or risk.clause_id
                excerpt = chunk.text[:320] + ("…" if len(chunk.text) > 320 else "")

            financial = financials_by_clause.get(risk.clause_id)
            clauses_payload.append(
                {
                    "id": risk.clause_id,
                    "title": title,
                    "severity": risk.severity,
                    "risk_type": risk.risk_type,
                    "confidence": risk.confidence,
                    "excerpt": excerpt,
                    "analysis": risk.raw_explanation,
                    "action": risk.action or None,
                    "ambiguous_terms": risk.ambiguous_terms or [],
                    "risk_of_interpretation": risk.risk_of_interpretation or "",
                    "financial": (
                        {
                            "exposure": financial.financial_exposure,
                            "conditions": financial.conditions,
                            "worst_case": financial.worst_case_cost,
                        }
                        if financial
                        else None
                    ),
                }
            )

        score = _document_risk_score([r.severity for r in risks])

        glossary_qs = document.glossary_entries.all()
        # External references are stored on the row but the user may have
        # disabled them after the run completed — honor the current preference.
        glossary_payload = GlossaryEntrySerializer(glossary_qs, many=True).data
        if not document.include_external_references:
            for entry in glossary_payload:
                entry["external_references"] = []

        return Response(
            {
                "id": str(document.id),
                "name": document.original_filename,
                "type": document.document_type or "Document",
                "status": document.status,
                "chunking_strategy": document.chunking_strategy,
                "score": score,
                "clause_count": len(chunks),
                "clauses": clauses_payload,
                "financial_risks": FinancialRiskSerializer(
                    document.financial_risks.all(), many=True
                ).data,
                "glossary": glossary_payload,
                "include_external_references": document.include_external_references,
                "created_at": document.created_at.isoformat(),
                "completed_at": document.completed_at.isoformat() if document.completed_at else None,
            }
        )


class DocumentRisksView(APIView):
    """Raw risk findings for a document."""

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)
        findings = document.risk_findings.all()
        return Response(RiskFindingSerializer(findings, many=True).data)


class DocumentFinancialRisksView(APIView):
    """Raw financial risk findings for a document."""

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)
        risks = document.financial_risks.all()
        return Response(FinancialRiskSerializer(risks, many=True).data)


class DocumentGlossaryView(APIView):
    """Glossary entries for a document, with optional external references."""

    permission_classes = [IsAuthenticated]

    def get(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)
        entries = document.glossary_entries.all()
        data = GlossaryEntrySerializer(entries, many=True).data
        # Suppress external references at read-time if the user has them disabled,
        # even if older rows still carry stored URLs.
        if not document.include_external_references:
            for entry in data:
                entry["external_references"] = []
        return Response({
            "include_external_references": document.include_external_references,
            "entries": data,
        })


class DocumentPreferencesView(APIView):
    """
    Update user-controllable per-document preferences.

    PATCH body: {"include_external_references": true|false}

    Toggling the flag does NOT re-run the glossary agent — it only changes
    whether stored external references are surfaced in API responses going
    forward, and whether the next run (if any) generates new ones.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)
        serializer = DocumentPreferencesSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        document.include_external_references = serializer.validated_data[
            "include_external_references"
        ]
        document.save(update_fields=["include_external_references"])

        return Response({
            "id": str(document.id),
            "include_external_references": document.include_external_references,
        })


class DocumentQueryView(APIView):
    """
    Q&A over a document. Embeds the question, runs hybrid search over its chunks,
    and synthesizes an answer using Gemini Flash grounded in the top results.

    POST body: {"question": "..."}
    """

    permission_classes = [IsAuthenticated]
    _TOP_K = 5

    def post(self, request, document_id):
        document = get_object_or_404(Document, id=document_id, user=request.user)

        if document.status != DocumentStatus.COMPLETED:
            return Response(
                {"detail": f"Document is not ready yet (status={document.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = QuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]

        query_vector = embed_texts([question])[0]

        results = search_chunks(
            query_vector=query_vector,
            query_text=question,
            user_id=str(request.user.id),
            document_id=str(document.id),
            limit=self._TOP_K,
        )

        if not results:
            return Response(
                {
                    "answer": "I couldn't find anything in this document that answers your question.",
                    "sources": [],
                }
            )

        context_blocks = []
        for r in results:
            section = r.get("section") or r.get("chunk_id") or "clause"
            text = (r.get("text") or "")[:1200]
            context_blocks.append(f"[{section}]\n{text}")
        context = "\n\n---\n\n".join(context_blocks)

        prompt = (
            "You are LexGuard, a contract assistant. Answer the user's question using ONLY the "
            "clauses below from their document. If the answer is not in the clauses, say so plainly. "
            "Quote short phrases from the clauses when relevant.\n\n"
            f"Clauses:\n{context}\n\n"
            f"Question: {question}\n\n"
            "Answer:"
        )

        completion = _mistral_client().chat.completions.create(
            model=settings.MISTRAL_FLASH_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = (completion.choices[0].message.content or "").strip() or (
            "I couldn't generate an answer."
        )

        sources = [
            {
                "chunk_id": r.get("chunk_id"),
                "section": r.get("section"),
                "score": r.get("score"),
            }
            for r in results
        ]
        return Response({"answer": answer, "sources": sources})
