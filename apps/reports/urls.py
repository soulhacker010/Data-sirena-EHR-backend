from django.urls import path
from .views import (
    SessionSummaryView, BillingSummaryView, AuthorizationReportView,
    MissingNotesView, AnalyticsView,
)

urlpatterns = [
    path('reports/session-summary/', SessionSummaryView.as_view(), name='report-sessions'),
    path('reports/billing-summary/', BillingSummaryView.as_view(), name='report-billing'),
    path('reports/authorizations/', AuthorizationReportView.as_view(), name='report-authorizations'),
    path('reports/missing-notes/', MissingNotesView.as_view(), name='report-missing-notes'),
    path('reports/analytics/', AnalyticsView.as_view(), name='report-analytics'),
]
