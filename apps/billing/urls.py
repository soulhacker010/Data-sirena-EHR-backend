from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet, PaymentViewSet, ClaimViewSet, ClientClaimsView, CPTSuggestionView, ModifierSuggestionView, PayerViewSet

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'claims', ClaimViewSet, basename='claim')
router.register(r'payers', PayerViewSet, basename='payer')

urlpatterns = [
    path('', include(router.urls)),
    path('clients/<uuid:client_id>/claims/', ClientClaimsView.as_view(), name='client-claims'),
    path('cpt-suggestions/', CPTSuggestionView.as_view(), name='cpt-suggestions'),
    path('modifier-suggestions/', ModifierSuggestionView.as_view(), name='modifier-suggestions'),
]
