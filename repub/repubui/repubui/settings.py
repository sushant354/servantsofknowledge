import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = os.getenv('BASE_DIR')
if BASE_DIR == None:
    BASE_DIR = Path(__file__).resolve().parent.parent

# Add the repub directory to Python path for importing repub modules
REPUB_DIR = BASE_DIR.parent
if str(REPUB_DIR) not in sys.path:
    sys.path.insert(0, str(REPUB_DIR))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-default-key-for-development')
# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'False') == 'True' 
DEPLOYMENT = os.getenv('DEPLOYMENT', 'local')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
DJANGO_SETTINGS_MODULE="repubui.settings"
# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'repub_interface',
    'rest_framework',
    'rest_framework.authtoken',
]

# REST Framework configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.AllowAllUsersModelBackend']

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'repubui.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
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

WSGI_APPLICATION = 'repubui.wsgi.application'
# Database
DATABASE_OPTIONS = {
    'local': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    },
    'prod': {
        'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.postgresql'),
        'NAME': os.getenv('DB_NAME', 'repubdb'),
        'USER': os.getenv('DB_USER', 'repub'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

DATABASES = {'default': DATABASE_OPTIONS[DEPLOYMENT]}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]
# Email configuration
# For development, using console backend to see emails in console
# For production, configure your preferred email service
if os.getenv('POSTMARK_TOKEN'):
    # Use Postmark in production if token is available
    EMAIL_BACKEND = 'postmarker.django.EmailBackend'
    POSTMARK = {
        'TOKEN': os.getenv('POSTMARK_TOKEN'),
        'TEST_MODE': False,
        'VERBOSITY': 0,
    }
else:
    # Use console backend for development
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static_files')
]

# Media files (uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication settings
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

DEFAULT_FROM_EMAIL = os.getenv('FROM_EMAIL') 
SERVER_EMAIL = DEFAULT_FROM_EMAIL 
EMAIL_HOST_USER = DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = '[repub]'

# Session security settings
SESSION_COOKIE_AGE = 24*30*3600  # 30 days 
SESSION_EXPIRE_AT_BROWSER_CLOSE = False 

# Security settings - only enabled in production (when DEBUG=False)
if not DEBUG:
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 3600
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = 'same-origin'

# Logging configuration
# Configure handlers based on deployment environment
LOG_LEVEL =  os.getenv('LOG_LEVEL', 'INFO')
if DEPLOYMENT == 'local':
    # Use stream logging for local development
    DEFAULT_HANDLERS = ['console']
else:
    # Use file logging for production
    DEFAULT_HANDLERS = ['file']

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
        'repub_format': {
            'format': '{asctime} {name} {levelname} {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': os.getenv('LOGFILE', os.path.join(BASE_DIR, 'logs', 'repubui.log')),
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'repub_interface': {
            'handlers': DEFAULT_HANDLERS,
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'repubui': {
            'handlers': DEFAULT_HANDLERS,
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django': {
            'handlers': DEFAULT_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
    },
}
