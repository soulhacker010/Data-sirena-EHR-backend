"""Production-specific settings for Render deployment."""
import logging as _logging
import os
from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', '').split(',')

# ─── Sentry (PHI-scrubbed error tracking) ─────────────────────────────────────
# Initialised here, not in base.py, so dev runs never call out to Sentry.
# Gated on SENTRY_DSN — unset → no init, no network calls, no surprises.
# scrub_event / scrub_breadcrumb in apps.core.sentry strip every PHI field
# defined in PHI_FIELD_NAMES before any payload leaves the box.
SENTRY_DSN = os.getenv('SENTRY_DSN', '')
SENTRY_ENVIRONMENT = os.getenv('SENTRY_ENVIRONMENT', 'production')
SENTRY_RELEASE = os.getenv('RENDER_GIT_COMMIT', '')[:12]  # Render exposes this

if SENTRY_DSN:
    from apps.core.sentry import init_sentry
    if init_sentry(SENTRY_DSN, SENTRY_ENVIRONMENT, SENTRY_RELEASE):
        _logging.getLogger(__name__).info(
            'Sentry initialised (env=%s, release=%s)',
            SENTRY_ENVIRONMENT, SENTRY_RELEASE or '<unset>',
        )
    else:
        _logging.getLogger(__name__).warning(
            'SENTRY_DSN set but sentry-sdk not installed — pip install -r requirements.txt'
        )

# ─── Security Headers ─────────────────────────────────────────────────────────

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Render terminates TLS at the proxy — trust the X-Forwarded-Proto header
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ─── CSRF Trusted Origins ─────────────────────────────────────────────────────

CSRF_TRUSTED_ORIGINS = os.getenv(
    'CSRF_TRUSTED_ORIGINS', ''
).split(',')

# ─── Cache (Redis) ─────────────────────────────────────────────────────────────
# Required in prod for DRF throttling to work *globally* across gunicorn
# workers. The default LocMemCache is per-process, so on a multi-worker
# Render service the login throttle would reset every time a request hit
# a different worker — effectively bypassable. Redis is already provisioned
# for Celery; reuse it on a separate logical DB.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.getenv('CACHE_REDIS_URL', os.getenv('REDIS_URL', '').replace('/0', '/1')),
        'TIMEOUT': 300,
        'KEY_PREFIX': 'sirena',
    },
}

# ─── Static Files (WhiteNoise) ────────────────────────────────────────────────

STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# ─── Logging ───────────────────────────────────────────────────────────────────

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
