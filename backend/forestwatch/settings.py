import os
import sys
from ipaddress import IPv4Network
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent / 'frontend'

# Auto-load variables from .env in repository root if present
_env_path = BASE_DIR.parent / '.env'
if _env_path.is_file():
    for _line in _env_path.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            _k, _v = _k.strip(), _v.strip().strip("'\"")
            if _k and _k not in os.environ:
                os.environ[_k] = _v
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-demo-only-change-before-deployment')
if not DEBUG and SECRET_KEY == 'local-demo-only-change-before-deployment':
    raise RuntimeError('Set DJANGO_SECRET_KEY for deployment.')
_raw_hosts = os.getenv('DJANGO_ALLOWED_HOSTS', '*').strip()
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(',') if h.strip()]
if DEBUG and '*' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('*')
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
                 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'monitor']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'forestwatch.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [FRONTEND_DIR / 'templates'],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request',
              'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages',
              'monitor.context_processors.auth_options']}}]
WSGI_APPLICATION = 'forestwatch.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('DATABASE_PATH', str(BASE_DIR / 'db.sqlite3'))}}
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
                            {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}]
LANGUAGE_CODE = 'vi'
TIME_ZONE = os.getenv('TIME_ZONE', 'Asia/Ho_Chi_Minh')
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [FRONTEND_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# Evidence is served by an authenticated view, not a public media directory.
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media')))
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTHENTICATION_BACKENDS = [
    'monitor.auth_backend.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',
]
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = os.getenv('LOGIN_REDIRECT_URL', 'dashboard')
LOGOUT_REDIRECT_URL = 'home'
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
GOOGLE_OAUTH_REDIRECT_URI = os.getenv('GOOGLE_OAUTH_REDIRECT_URI', '').strip()
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
INFERENCE_MODE = os.getenv('INFERENCE_MODE', 'fosp').lower()
MODEL_PATH = os.getenv('MODEL_PATH', str(BASE_DIR / 'weights' / 'best.pt'))
MODEL_CONFIDENCE = float(os.getenv('MODEL_CONFIDENCE', '0.35'))
FOSP_MODEL_PATH = Path(os.getenv('FOSP_MODEL_PATH', str(BASE_DIR / 'weights' / 'fosp_best.pth')))
FOSP_RUNTIME_DIR = Path(os.getenv('FOSP_RUNTIME_DIR', str(BASE_DIR.parent / '.fosp-runtime')))
# FoSp-Gate (E6) checkpoints only. Defaults are the values chosen on validation: zoom TTA with pixel threshold 0.3;
# with zoom the validation rule returned gate 0.0 (no mask erased), without zoom it returned 0.9989 at threshold 0.4.
FOSP_GATE_TTA = os.getenv('FOSP_GATE_TTA', 'zoom').lower()
FOSP_GATE_TTA_THRESHOLD = float(os.getenv('FOSP_GATE_TTA_THRESHOLD', '0.3'))
FOSP_GATE_THRESHOLD = float(os.getenv('FOSP_GATE_THRESHOLD', '0.0' if FOSP_GATE_TTA == 'zoom' else '0.9989'))
PHONE_CAMERA_NETWORKS = tuple(
    IPv4Network(value.strip()) for value in os.getenv('PHONE_CAMERA_NETWORKS', '').split(',') if value.strip()
)
ALLOW_LAN_CAMERAS = os.getenv('ALLOW_LAN_CAMERAS', '1' if DEBUG else '0') == '1'

_CACHE_DIR = Path(os.getenv('CACHE_DIR', str(BASE_DIR.parent / 'work/cache')))
if 'test' in sys.argv:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
else:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': str(_CACHE_DIR),
            'OPTIONS': {'MAX_ENTRIES': 2000},
        }
    }

EMAIL_HOST = os.getenv('EMAIL_HOST', '').strip()
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '').strip()
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '').strip()
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', '1') == '1'
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'noreply@forestwatch.local')
EMAIL_BACKEND = (
    'django.core.mail.backends.smtp.EmailBackend'
    if EMAIL_HOST and EMAIL_HOST_USER
    else 'django.core.mail.backends.console.EmailBackend'
)

SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
