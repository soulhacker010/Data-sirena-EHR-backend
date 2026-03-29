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

# ─── Stripe ─────────────────────────────────────────────────────────────────────

STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_FEE_PASSTHROUGH = os.getenv('STRIPE_FEE_PASSTHROUGH', 'false').lower() == 'true'

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

# ─── Application ───────────────────────────────────────────────────────────────

FRONTEND_BASE_URL = os.getenv('FRONTEND_BASE_URL', 'http://localhost:5173')
DJANGO_ADMIN_URL = os.getenv('DJANGO_ADMIN_URL', 'admin')
