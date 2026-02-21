"""
Recurrence generator for creating recurring appointment series.

Handles daily, weekly, biweekly, and monthly patterns.
"""
from datetime import timedelta
from django.utils import timezone


class RecurrenceGenerator:
    """Generates appointment instances from a recurrence pattern."""

    @staticmethod
    def generate(appointment, pattern):
        """
        Create recurring appointment instances from a pattern.

        Args:
            appointment: The template appointment to clone
            pattern: Dict with recurrence config:
                {
                    "frequency": "weekly",
                    "days": [1, 3, 5],
                    "end_date": "2026-06-30",
                    "series_id": "uuid"
                }

        Returns:
            List of Appointment instances (not yet saved)
        """
        from .models import Appointment
        from datetime import datetime

        frequency = pattern.get('frequency', 'weekly')
        end_date_str = pattern.get('end_date')
        days = pattern.get('days', [])

        if not end_date_str:
            return []

        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        instances = []
        current_date = appointment.start_time.date()
        duration = appointment.end_time - appointment.start_time

        while current_date <= end_date:
            # Advance based on frequency
            if frequency == 'daily':
                current_date += timedelta(days=1)
            elif frequency == 'weekly':
                current_date += timedelta(days=7)
            elif frequency == 'biweekly':
                current_date += timedelta(days=14)
            elif frequency == 'monthly':
                # Move to same day next month
                month = current_date.month + 1
                year = current_date.year
                if month > 12:
                    month = 1
                    year += 1
                try:
                    current_date = current_date.replace(year=year, month=month)
                except ValueError:
                    # Handle months with fewer days
                    current_date = current_date.replace(
                        year=year, month=month + 1, day=1
                    ) - timedelta(days=1)
            else:
                break

            if current_date > end_date:
                break

            # For weekly/biweekly with specific days, check if current day matches
            if frequency in ('weekly', 'biweekly') and days:
                if current_date.isoweekday() not in days:
                    continue

            # Create the instance
            new_start = appointment.start_time.replace(
                year=current_date.year,
                month=current_date.month,
                day=current_date.day,
            )
            new_end = new_start + duration

            instance = Appointment(
                organization=appointment.organization,
                client=appointment.client,
                provider=appointment.provider,
                location=appointment.location,
                authorization=appointment.authorization,
                start_time=new_start,
                end_time=new_end,
                service_code=appointment.service_code,
                units=appointment.units,
                status='scheduled',
                is_recurring=True,
                recurrence_pattern=pattern,
            )
            instances.append(instance)

        return instances
