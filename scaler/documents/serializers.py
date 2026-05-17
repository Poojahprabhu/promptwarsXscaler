import logging

from django.conf import settings
from rest_framework import serializers

from .models import Document
from .models import DocumentChunk
from .models import FinancialRisk
from .models import GlossaryEntry
from .models import RiskFinding

logger = logging.getLogger(__name__)

ALLOWED_MIME_TYPES = frozenset(settings.DOCUMENT_ALLOWED_MIME_TYPES)
MAX_UPLOAD_SIZE = settings.DOCUMENT_MAX_UPLOAD_SIZE_BYTES


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    include_external_references = serializers.BooleanField(required=False, default=True)

    def validate_file(self, value):
        if value.size > MAX_UPLOAD_SIZE:
            max_mb = MAX_UPLOAD_SIZE // (1024 * 1024)
            raise serializers.ValidationError(f"File too large. Maximum allowed size is {max_mb} MB.")

        content_type = getattr(value, "content_type", "")
        if content_type not in ALLOWED_MIME_TYPES:
            raise serializers.ValidationError(
                f"Unsupported file type '{content_type}'. Allowed: PDF, DOCX."
            )
        return value


class DocumentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id",
            "original_filename",
            "file_format",
            "status",
            "document_type",
            "chunking_strategy",
            "error_message",
            "include_external_references",
            "created_at",
            "completed_at",
        ]
        read_only_fields = fields


class DocumentChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentChunk
        fields = [
            "id",
            "chunk_id",
            "section",
            "text",
            "page_refs",
            "images",
            "has_table",
            "has_signature",
            "chunk_metadata",
            "qdrant_point_id",
            "created_at",
        ]
        read_only_fields = fields


class SearchSerializer(serializers.Serializer):
    query = serializers.CharField(min_length=1, max_length=2000)
    document_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(min_value=1, max_value=50, default=10)


class QuerySerializer(serializers.Serializer):
    question = serializers.CharField(min_length=1, max_length=2000)


class RiskFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskFinding
        fields = [
            "id",
            "clause_id",
            "risk_type",
            "severity",
            "confidence",
            "raw_explanation",
            "action",
            "ambiguous_terms",
            "risk_of_interpretation",
            "created_at",
        ]
        read_only_fields = fields


class GlossaryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = GlossaryEntry
        fields = [
            "id",
            "term",
            "definition",
            "context",
            "clause_ids",
            "external_references",
            "created_at",
        ]
        read_only_fields = fields


class DocumentPreferencesSerializer(serializers.Serializer):
    """Patch-style serializer for user-toggleable document preferences."""
    include_external_references = serializers.BooleanField()


class FinancialRiskSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialRisk
        fields = [
            "id",
            "clause_id",
            "financial_exposure",
            "conditions",
            "worst_case_cost",
            "created_at",
        ]
        read_only_fields = fields
