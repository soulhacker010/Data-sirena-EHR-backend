"""Development-specific settings."""
from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# Allow browsable API in development
REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] = [  # noqa: F405
    'rest_framework.renderers.JSONRenderer',
    'rest_framework.renderers.BrowsableAPIRenderer',
]

# Force the in-memory Channels layer in dev so the BLS WebSockets work on a
# laptop without Redis installed. base.py would otherwise pick Redis whenever
# REDIS_URL is set in .env (which it has to be for Celery). Single-process
# `runserver` only — group state is per-process and won't span workers.
CHANNEL_LAYERS = {  # noqa: F405
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}
