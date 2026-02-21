"""
Celery configuration for Sirena Health EHR.

Auto-discovers tasks from all installed apps.
"""
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

app = Celery('sirena_ehr')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for verifying Celery is working."""
    print(f'Request: {self.request!r}')
