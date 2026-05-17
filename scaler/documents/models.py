import uuid

from django.conf import settings
from django.db import models


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    file = models.FileField(upload_to="documents/")
    original_filename = models.CharField(max_length=255)
    file_format = models.CharField(max_length=20, blank=True)  # pdf, docx
    status = models.CharField(
        max_length=20,
        choices=DocumentStatus.choices,
        default=DocumentStatus.PENDING,
        db_index=True,
    )
    document_type = models.CharField(max_length=100, blank=True)
    chunking_strategy = models.CharField(max_length=100, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    include_external_references = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename} ({self.status})"

    @property
    def gcs_uri(self) -> str:
        return f"gs://{settings.GCP_STORAGE_BUCKET_NAME}/{self.file.name}"


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_id = models.CharField(max_length=255)  # e.g. "clause_4_2"
    section = models.CharField(max_length=255, blank=True)
    text = models.TextField()
    page_refs = models.JSONField(default=list)       # [3, 4]
    images = models.JSONField(default=list)          # ["gs://bucket/..."]
    has_table = models.BooleanField(default=False)
    has_signature = models.BooleanField(default=False)
    chunk_metadata = models.JSONField(default=dict)
    qdrant_point_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["chunk_id"]

    def __str__(self):
        return f"{self.document.original_filename} / {self.chunk_id}"


class RiskSeverity(models.TextChoices):
    CRITICAL = "critical", "Critical"
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"
    SAFE = "safe", "Safe"


class RiskFinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="risk_findings",
    )
    clause_id = models.CharField(max_length=255, db_index=True)
    risk_type = models.CharField(max_length=255)
    severity = models.CharField(
        max_length=20,
        choices=RiskSeverity.choices,
        db_index=True,
    )
    confidence = models.FloatField(default=0.5)
    raw_explanation = models.TextField()
    action = models.TextField(blank=True)
    ambiguous_terms = models.JSONField(default=list)
    risk_of_interpretation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "clause_id"]

    def __str__(self):
        return f"{self.document.original_filename} / {self.clause_id} [{self.severity}]"


class GlossaryEntry(models.Model):
    """A plain-English definition for a complex or legal term found in the document."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="glossary_entries",
    )
    term = models.CharField(max_length=255, db_index=True)
    definition = models.TextField()
    context = models.TextField(blank=True)
    clause_ids = models.JSONField(default=list)
    # [{"title": "...", "url": "...", "source": "wikipedia|investopedia|cornell_lii|..."}]
    external_references = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "term"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "term"],
                name="unique_term_per_document",
            ),
        ]

    def __str__(self):
        return f"{self.document.original_filename} / {self.term}"


class FinancialRisk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="financial_risks",
    )
    clause_id = models.CharField(max_length=255, db_index=True)
    financial_exposure = models.TextField()
    conditions = models.TextField()
    worst_case_cost = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document", "clause_id"]

    def __str__(self):
        return f"{self.document.original_filename} / {self.clause_id} [financial]"
