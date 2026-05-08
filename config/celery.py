"""
Celery configuration for Sirena Health EHR.

Auto-discovers tasks from all installed apps.
"""
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

app = Celery('sirena_ehr')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


# ─── Beat schedule ──────────────────────────────────────────────────────────
# Poll Office Ally /outbound every 15 minutes for 999, 277CA, 835, and
# File Summary responses. The task no-ops if OA_SFTP_* env vars are unset,
# so this is safe to ship before credentials arrive.
app.conf.beat_schedule = {
    'poll-office-ally-outbound': {
        'task': 'apps.billing.tasks.poll_oa_outbound',
        'schedule': crontab(minute='*/15'),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for verifying Celery is working."""
    print(f'Request: {self.request!r}')
