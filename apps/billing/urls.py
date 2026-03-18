from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet, PaymentViewSet, ClaimViewSet, ClientClaimsView
from .webhooks import stripe_webhook

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'claims', ClaimViewSet, basename='claim')

urlpatterns = [
    path('', include(router.urls)),
    path('clients/<uuid:client_id>/claims/', ClientClaimsView.as_view(), name='client-claims'),
    path('webhooks/stripe/', stripe_webhook, name='stripe-webhook'),
]
