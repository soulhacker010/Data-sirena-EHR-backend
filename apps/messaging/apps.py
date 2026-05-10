from django.apps import AppConfig


class MessagingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.messaging'
    verbose_name = 'Messaging (SMS / Outbound)'

    def ready(self):
        # Import signal handlers so post_save on Appointment wires up reminders.
        # Local import is the Django-recommended way to avoid AppRegistryNotReady
        # errors during early startup.
        from . import signals  # noqa: F401
