"""
Appointment views — coordinated with frontend api/appointments.ts.

GET    /api/v1/appointments/              → list (calendar view, filterable)
POST   /api/v1/appointments/              → create (single or recurring)
GET    /api/v1/appointments/{id}/         → detail
PUT    /api/v1/appointments/{id}/         → update
DELETE /api/v1/appointments/{id}/         → cancel
POST   /api/v1/appointments/{id}/status/  → update status only
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.db import transaction
from django.db.models import F

from apps.core.permissions import IsFrontDesk
from .models import Appointment
from .serializers import (
    AppointmentSerializer,
    AppointmentCreateSerializer,
    AppointmentListSerializer,
    AppointmentStatusSerializer,
)
from .services import RecurrenceGenerator


class AppointmentViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for appointments with recurring support.

    Filters: client, provider, status, start_time range
    """
    permission_classes = [IsAuthenticated, IsFrontDesk]
    pagination_class = None  # Calendar views need a flat array, not paginated
    filterset_fields = ['client', 'provider', 'status', 'location']
    search_fields = ['notes', 'service_code']
    ordering_fields = ['start_time']

    def get_queryset(self):
        qs = Appointment.objects.filter(
            organization=self.request.user.organization
        ).select_related('client', 'provider', 'location', 'authorization')

        # Date range filtering for calendar views
        # Frontend sends start_date / end_date (AppointmentFilters type)
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            qs = qs.filter(start_time__gte=start_date)
        if end_date:
            qs = qs.filter(start_time__lte=end_date)

        # Provider / client / status filtering
        provider_id = self.request.query_params.get('provider_id')
        client_id = self.request.query_params.get('client_id')
        appt_status = self.request.query_params.get('status')
        if provider_id:
            qs = qs.filter(provider_id=provider_id)
        if client_id:
            qs = qs.filter(client_id=client_id)
        if appt_status:
            qs = qs.filter(status=appt_status)

        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return AppointmentListSerializer
        if self.action == 'create':
            return AppointmentCreateSerializer
        return AppointmentSerializer

    def create(self, request, *args, **kwargs):
        """Override to include treatment plan warning in response."""
        self._treatment_plan_warning = False
        response = super().create(request, *args, **kwargs)
        if self._treatment_plan_warning:
            response.data['warning'] = (
                'This client has no active treatment plan. '
                'Consider creating one before ongoing sessions.'
            )
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        appointment = self.get_object()
        self._send_appointment_email(appointment, event='updated')
        return response

    def destroy(self, request, *args, **kwargs):
        appointment = self.get_object()
        self._send_appointment_email(appointment, event='cancelled')
        return super().destroy(request, *args, **kwargs)

    def _send_appointment_email(self, appointment, *, event: str):
        try:
            from apps.core.email import EmailService
            org_name = self.request.user.organization.name if self.request.user.organization else 'Sirena Health'
            EmailService.send_appointment_email(appointment, event=event, org_name=org_name)
        except Exception:
            pass

    def perform_create(self, serializer):
        """
        FIX CT-3: Validate that client_id and provider_id belong to the user's org
        before creating the appointment. Without this, a malicious request
        could reference a client or provider from another organization.
        """
        from apps.clients.models import Client
        from apps.accounts.models import User

        org = self.request.user.organization
        validated = serializer.validated_data

        # Validate client belongs to org
        client_id = validated.get('client_id')
        if client_id and not Client.objects.filter(id=client_id, organization=org).exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'client_id': 'Client does not belong to your organization.'})

        # Validate provider belongs to org
        provider_id = validated.get('provider_id')
        if provider_id and not User.objects.filter(id=provider_id, organization=org).exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'provider_id': 'Provider does not belong to your organization.'})

        # FIX SC-1: Double-booking prevention — check for overlapping appointments
        start_time = validated.get('start_time')
        end_time = validated.get('end_time')
        if provider_id and start_time and end_time:
            overlap = Appointment.objects.filter(
                provider_id=provider_id,
                organization=org,
                status__in=['scheduled', 'attended'],
                start_time__lt=end_time,
                end_time__gt=start_time,
            ).exists()
            if overlap:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({
                    'provider_id': 'This provider already has an appointment during this time slot.'
                })

        # HARDENING: Block scheduling if linked authorization is fully used
        auth_id = validated.get('authorization_id')
        if auth_id:
            from apps.clients.models import Authorization
            try:
                auth = Authorization.objects.get(pk=auth_id)
                if auth.units_remaining <= 0 and self.request.user.role != 'admin':
                    from rest_framework.exceptions import ValidationError
                    raise ValidationError({
                        'authorization': (
                            f'Authorization #{auth.authorization_number} is fully used '
                            f'({auth.units_used}/{auth.units_approved} units). '
                            f'Only admins can override this.'
                        )
                    })
            except Authorization.DoesNotExist:
                pass

        appointment = serializer.save(organization=org)

        # HARDENING: Warn if client has no active treatment plan
        client_id = validated.get('client_id')
        if client_id:
            from apps.clinical.models import TreatmentPlan
            has_plan = TreatmentPlan.objects.filter(
                client_id=client_id,
                is_active=True,
            ).exists()
            if not has_plan:
                self._treatment_plan_warning = True

        # Generate recurring instances if pattern provided
        if appointment.is_recurring and appointment.recurrence_pattern:
            instances = RecurrenceGenerator.generate(
                appointment, appointment.recurrence_pattern
            )
            if instances:
                Appointment.objects.bulk_create(instances)

        self._send_appointment_email(appointment, event='scheduled')

    @action(detail=True, methods=['post'], url_path='status')
    def update_status(self, request, pk=None):
        """
        POST /api/v1/appointments/{id}/status/ — update status only.

        FIX SC-2: Authorization units decrement uses F() expression for
        atomic increment, wrapped in transaction.atomic(). Two concurrent
        requests won't overwrite each other's units.
        """
        appointment = self.get_object()
        serializer = AppointmentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_status = appointment.status
        new_status = serializer.validated_data['status']

        with transaction.atomic():
            appointment.status = new_status
            appointment.save(update_fields=['status', 'updated_at'])

            # Auto-decrement authorization units if marked as attended
            if (
                new_status == 'attended'
                and old_status != 'attended'
                and appointment.authorization_id
            ):
                from apps.clients.models import Authorization
                units = float(appointment.units or 1)

                # FIX SC-2: Use F() for atomic increment — race-condition safe
                Authorization.objects.filter(
                    pk=appointment.authorization_id
                ).update(
                    units_used=F('units_used') + units
                )

                # Clamp to units_approved (in case of overshoot)
                Authorization.objects.filter(
                    pk=appointment.authorization_id,
                    units_used__gt=F('units_approved'),
                ).update(
                    units_used=F('units_approved')
                )

                # Auto-generate notification if utilization hit threshold
                try:
                    from apps.notifications.services import notify_authorization_utilization
                    auth = Authorization.objects.select_related(
                        'client', 'client__organization',
                    ).get(pk=appointment.authorization_id)
                    notify_authorization_utilization(auth)
                except Exception:
                    pass  # Never break the main flow for notifications

        appointment.refresh_from_db()
        if new_status == 'cancelled' and old_status != 'cancelled':
            self._send_appointment_email(appointment, event='cancelled')
        return Response(AppointmentSerializer(appointment).data)
