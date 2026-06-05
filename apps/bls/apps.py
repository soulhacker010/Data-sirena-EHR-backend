from django.apps import AppConfig


class BlsConfig(AppConfig):
    """Django app config for the Bilateral Stimulation (BLS) module."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.bls'
    verbose_name = 'Bilateral Stimulation'
