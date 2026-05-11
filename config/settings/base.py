"""
Django settings for Sirena Health EHR — BASE configuration.

Shared settings across all environments (development, production).
"""
import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Security
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-change-me')
DEBUG = os.getenv('DEBUG', 'true').lower() in ('true', '1', 'yes')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    'storages',

    # Project apps
    'apps.core',
    'apps.accounts',
    'apps.audit',
    'apps.clients',
    'apps.scheduling',
    'apps.clinical',
    'apps.billing',
    'apps.dashboard',
    'apps.reports',
    'apps.notifications',
    'apps.messaging',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Custom middleware (added after apps are ready)
    'apps.core.middleware.OrganizationMiddleware',
    'apps.core.middleware.AuditMiddleware',
    'apps.core.middleware.LastSeenMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database — PostgreSQL via dj-database-url
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL', 'postgres://postgres@localhost:5432/sirena_ehr'),
        conn_max_age=600,
        ssl_require=os.getenv('DATABASE_SSL_REQUIRE', 'false').lower() in ('true', '1', 'yes'),
    )
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'mediafiles'

# Default primary key
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Django REST Framework ──────────────────────────────────────────────────────

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.StandardResultsPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler',
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    # FIX RL-1: Global rate limiting — prevents brute force and API abuse
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': os.getenv('DRF_THROTTLE_USER', '100/min'),
        'anon': os.getenv('DRF_THROTTLE_ANON', '20/min'),
        'login': os.getenv('DRF_THROTTLE_LOGIN', '5/min'),
        'email': os.getenv('DRF_THROTTLE_EMAIL', '10/min'),
        # Token refresh: legit clients refresh roughly every 14 min when active.
        # 30/hour leaves headroom for tab-spam and short-lived multi-tab sessions
        # while blunting brute-force scans of leaked refresh tokens.
        'token_refresh': os.getenv('DRF_THROTTLE_TOKEN_REFRESH', '30/hour'),
    },
}

# ─── SimpleJWT ──────────────────────────────────────────────────────────────────

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=int(os.getenv('SIMPLE_JWT_ACCESS_LIFETIME_MINUTES', 15))
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        days=int(os.getenv('SIMPLE_JWT_REFRESH_LIFETIME_DAYS', 7))
    ),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# Session idle timeout (minutes). The token refresh view rejects refreshes
# from sessions whose User.last_seen_at is older than this. HIPAA-style
# guidance for clinical applications is 15 min; we picked 30 as a balance —
# clinicians shouldn't get kicked out mid-note for a slow paragraph.
IDLE_TIMEOUT_MINUTES = int(os.getenv('IDLE_TIMEOUT_MINUTES', 30))

# FIX #7: Use custom auth class that checks is_active on every request
# (default simplejwt only checks is_active at login time)
REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'] = [
    'apps.core.authentication.ActiveUserJWTAuthentication',
]

# ─── CORS ───────────────────────────────────────────────────────────────────────

# FIX CO-1: Explicitly deny CORS_ALLOW_ALL_ORIGINS to prevent accidental override
CORS_ALLOW_ALL_ORIGINS = False

CORS_ALLOWED_ORIGINS = os.getenv(
    'CORS_ALLOWED_ORIGINS', 'http://localhost:5173'
).split(',')

CORS_ALLOW_CREDENTIALS = True

# ─── Resend Email ───────────────────────────────────────────────────────────────

RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'no-reply@sirenahealthehr.com')
RESEND_REPLY_TO = os.getenv('RESEND_REPLY_TO', '')

# ─── AWS S3 (Document Storage) ─────────────────────────────────────────────────

AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', '')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'us-east-1')
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_QUERYSTRING_EXPIRE = 300  # Presigned URL TTL in seconds

# ─── Celery ─────────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

# ─── Office Ally (clearinghouse) ──────────────────────────────────────────────
# SFTP credentials for X12 837P claim submission. apps.billing.services.office_ally
# reads these via getattr(settings, 'OA_SFTP_*'); without them surfaced here,
# the env vars set in Render are invisible to Django and _is_configured()
# always returns False even when the values are present in os.environ.
OA_SFTP_HOST = os.getenv('OA_SFTP_HOST', '')
OA_SFTP_PORT = int(os.getenv('OA_SFTP_PORT', '22'))
OA_SFTP_USERNAME = os.getenv('OA_SFTP_USERNAME', '')
OA_SFTP_PASSWORD = os.getenv('OA_SFTP_PASSWORD', '')
OA_SFTP_INBOUND = os.getenv('OA_SFTP_INBOUND', '/inbound')
OA_SFTP_OUTBOUND = os.getenv('OA_SFTP_OUTBOUND', '/outbound')
# Production safety switch: until OA_GO_LIVE=true, generated claim files
# are prefixed OATEST_ so Office Ally parses them but does not forward to
# payers. Flip to true only after a successful test submission round.
OA_GO_LIVE = os.getenv('OA_GO_LIVE', 'false').lower() in ('true', '1', 'yes')

# ─── SMS / Messaging ───────────────────────────────────────────────────────────
# E26: outbound SMS appointment reminders. The provider abstraction
# (apps.messaging.providers) lets us run on the in-process StubProvider in
# dev / CI / pre-credentials production, and switch to Twilio by flipping
# SMS_PROVIDER=twilio plus the three Twilio env vars below.

SMS_PROVIDER = os.getenv('SMS_PROVIDER', 'stub')  # 'stub' | 'twilio'

# Reminder cadence — Dr. Joe asked for multi-step nudges, especially for
# first responders who need more than one. 48h gives enough time to reschedule;
# 24h is the standard "you have something tomorrow"; 2h is the day-of nudge.
SMS_REMINDER_LEAD_TIMES_HOURS = [48, 24, 2]

# How often the dispatcher beat task runs. 5 min keeps the queue fresh without
# hammering the DB; combined with quiet-hours shifting it gives ±5 min jitter
# which is fine for appointment reminders.
SMS_REMINDER_DISPATCH_INTERVAL_MINUTES = 5

# If a reminder didn't get sent within this many hours of its scheduled time
# (worker outage, beat restart), give up and skip rather than firing a stale
# "tomorrow" reminder six hours late.
SMS_REMINDER_STALE_WINDOW_HOURS = 1

# Quiet hours in practice-local time (America/New_York). (21, 8) = no sends
# from 9pm through 7:59am. Reminders that fall in this window are deferred
# to the start of the next non-quiet hour.
SMS_QUIET_HOURS_LOCAL = (21, 8)

# Twilio credentials — set in production env when account is provisioned.
# Empty string means "not configured"; TwilioProvider raises ProviderError
# at construction so the system fails loudly if SMS_PROVIDER=twilio without
# creds.
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN', '')
TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER', '')

# Practice callback number that appears in every reminder ("Questions: …").
PRACTICE_CALLBACK_PHONE = os.getenv('PRACTICE_CALLBACK_PHONE', '')

# ─── Application ───────────────────────────────────────────────────────────────

FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'http://localhost:5173')
DJANGO_ADMIN_URL = os.getenv('DJANGO_ADMIN_URL', 'admin')
