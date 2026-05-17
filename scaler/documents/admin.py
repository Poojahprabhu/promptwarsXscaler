from django.contrib import admin

from .models import Document
from .models import DocumentChunk


class DocumentChunkInline(admin.TabularInline):
    model = DocumentChunk
    extra = 0
    readonly_fields = ("id", "chunk_id", "section", "page_refs", "images", "qdrant_point_id", "created_at")
    fields = ("chunk_id", "section", "has_table", "has_signature", "qdrant_point_id")
    can_delete = False


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "user", "status", "document_type", "file_format", "created_at")
    list_filter = ("status", "file_format", "document_type")
    search_fields = ("original_filename", "user__email")
    readonly_fields = ("id", "celery_task_id", "created_at", "completed_at")
    inlines = [DocumentChunkInline]


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("chunk_id", "section", "document", "has_table", "has_signature", "created_at")
    list_filter = ("has_table", "has_signature")
    search_fields = ("chunk_id", "section", "text")
    readonly_fields = ("id", "qdrant_point_id", "created_at")
