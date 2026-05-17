from django.urls import path

from .api_views import DocumentAnalysisView
from .api_views import DocumentChunksView
from .api_views import DocumentFinancialRisksView
from .api_views import DocumentGlossaryView
from .api_views import DocumentListView
from .api_views import DocumentPreferencesView
from .api_views import DocumentQueryView
from .api_views import DocumentRisksView
from .api_views import DocumentSearchView
from .api_views import DocumentStatusView
from .api_views import DocumentUploadView

urlpatterns = [
    path("", DocumentListView.as_view(), name="document-list"),
    path("upload/", DocumentUploadView.as_view(), name="document-upload"),
    path("search/", DocumentSearchView.as_view(), name="document-search"),
    path("<uuid:document_id>/status/", DocumentStatusView.as_view(), name="document-status"),
    path("<uuid:document_id>/chunks/", DocumentChunksView.as_view(), name="document-chunks"),
    path("<uuid:document_id>/analysis/", DocumentAnalysisView.as_view(), name="document-analysis"),
    path("<uuid:document_id>/risks/", DocumentRisksView.as_view(), name="document-risks"),
    path(
        "<uuid:document_id>/financial-risks/",
        DocumentFinancialRisksView.as_view(),
        name="document-financial-risks",
    ),
    path(
        "<uuid:document_id>/glossary/",
        DocumentGlossaryView.as_view(),
        name="document-glossary",
    ),
    path(
        "<uuid:document_id>/preferences/",
        DocumentPreferencesView.as_view(),
        name="document-preferences",
    ),
    path("<uuid:document_id>/query/", DocumentQueryView.as_view(), name="document-query"),
]
