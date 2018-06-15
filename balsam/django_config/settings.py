"""
Django settings for argobalsam project.

Generated by 'django-admin startproject' using Django 1.9.1.

For more information on this file, see
https://docs.djangoproject.com/en/1.9/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.9/ref/settings/
"""

import json
import os
import sys
import shutil
import time
import subprocess
from balsam.django_config import serverinfo
from balsam.django_config.db_index import refresh_db_index
from django.core.management import call_command

home_dir = os.path.expanduser('~')
BALSAM_HOME = os.path.join(home_dir, '.balsam')
default_db_path = os.path.join(BALSAM_HOME , 'default_db')

def bootstrap():
    if not os.path.exists(default_db_path):
        os.makedirs(default_db_path, mode=0o755)
        time.sleep(1)

    addr_path = os.path.join(default_db_path, 'dbwriter_address')
    if not os.path.exists(addr_path):
        with open(addr_path, 'w') as fp: 
            fp.write('{"db_type": "sqlite3"}')

    user_settings_path = os.path.join(BALSAM_HOME, 'settings.json')
    if not os.path.exists(user_settings_path):
        here = os.path.dirname(os.path.abspath(__file__))
        default_settings_path = os.path.join(here, 'default_settings.json')
        shutil.copy(default_settings_path, user_settings_path)
        print("Created Balsam JSON settings at", user_settings_path)

    thismodule = sys.modules[__name__]
    user_settings = json.load(open(user_settings_path))
    for k, v in user_settings.items():
        setattr(thismodule, k, v)

# ---------------
# DATABASE SETUP
# ---------------
def resolve_db_path(path=None):
    if path:
        path = os.path.expanduser(path)
        assert os.path.exists(path)
    elif os.environ.get('BALSAM_DB_PATH'):
        path = os.environ['BALSAM_DB_PATH']
        assert os.path.exists(path), f"balsamDB path {path} not found"
    else:
        path = default_db_path

    path = os.path.expanduser(path)
    path = os.path.abspath(path)
    os.environ['BALSAM_DB_PATH'] = path
    return path

def configure_db_backend(db_path):
    ENGINES = {
        'sqlite3' : 'django.db.backends.sqlite3',
        'postgres': 'django.db.backends.postgresql_psycopg2',
    }
    NAMES = {
        'sqlite3' : os.path.join(db_path, 'db.sqlite3'),
        'postgres': 'balsam',
    }
    OPTIONS = {
        'sqlite3' : {'timeout' : 5000},
        'postgres' : {},
    }

    info = serverinfo.ServerInfo(db_path)
    db_type = info['db_type']
    user = info.get('user', '')
    password = info.get('password', '')
    host = info.get('host', '')
    port = info.get('port', '')

    db_name = NAMES[db_type]

    db = dict(ENGINE=ENGINES[db_type], NAME=db_name,
              OPTIONS=OPTIONS[db_type], USER=user, PASSWORD=password,
              HOST=host, PORT=port, CONN_MAX_AGE=60)

    DATABASES = {'default':db}
    return DATABASES
    
bootstrap()
CONCURRENCY_ENABLED = True
BALSAM_PATH = resolve_db_path()
DATABASES = configure_db_backend(BALSAM_PATH)

db_type = DATABASES['default']['ENGINE']
db_name = DATABASES['default']['NAME']
if os.environ['BALSAM_DB_PATH'] == default_db_path and not os.path.exists(db_name) and 'BALSAM_BOOTSTRAP' not in os.environ:
    print("Bootstrapping sqlite DB at", db_name)
    here = os.path.dirname(os.path.abspath(__file__))
    initpath = os.path.join(os.path.dirname(here), 'scripts', 'init.py')
    cmd = f"{sys.executable} {initpath}"
    proc = subprocess.run(f'BALSAM_BOOTSTRAP=1 {cmd} {BALSAM_PATH}', shell=True,
                   check=False, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        print(proc.stdout.decode('utf-8'))
        raise RuntimeError
    refresh_db_index()

# --------------------
# SUBDIRECTORY SETUP
# --------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGGING_DIRECTORY = os.path.join(BALSAM_PATH , 'log') 
DATA_PATH = os.path.join(BALSAM_PATH ,'data')
BALSAM_WORK_DIRECTORY = DATA_PATH

for d in [
      BALSAM_PATH ,
      DATA_PATH,
      LOGGING_DIRECTORY,
      BALSAM_WORK_DIRECTORY,
]:
    if not os.path.exists(d):
        os.makedirs(d)

# ----------------
# LOGGING SETUP
# ----------------
HANDLER_FILE = os.path.join(LOGGING_DIRECTORY, LOG_FILENAME)
BALSAM_DB_CONFIG_LOG = os.path.join(LOGGING_DIRECTORY, "db.log")
LOGGING = {
   'version': 1,
   'disable_existing_loggers': False,
   'formatters': {
      'standard': {
      'format' : '%(asctime)s|%(process)d|%(levelname)8s|%(name)s:%(lineno)s] %(message)s',
      'datefmt' : "%d-%b-%Y %H:%M:%S"
      },
   },
   'handlers': {
      'console': {
         'class':'logging.StreamHandler',
         'formatter': 'standard',
          'level' : 'DEBUG'
      },
      'default': {
         'level':LOG_HANDLER_LEVEL,
         'class':'logging.handlers.RotatingFileHandler',
         'filename': HANDLER_FILE,
         'maxBytes': LOG_FILE_SIZE_LIMIT,
         'backupCount': LOG_BACKUP_COUNT,
         'formatter': 'standard',
      },
      'balsam-db-config': {
         'level':LOG_HANDLER_LEVEL,
         'class':'logging.handlers.RotatingFileHandler',
         'filename': BALSAM_DB_CONFIG_LOG,
         'maxBytes': LOG_FILE_SIZE_LIMIT,
         'backupCount': LOG_BACKUP_COUNT,
         'formatter': 'standard',
      },
      #'django': {
      #   'level': LOG_HANDLER_LEVEL,
      #   'class':'logging.handlers.RotatingFileHandler',
      #   'filename': os.path.join(LOGGING_DIRECTORY, 'django.log'),
      #   'maxBytes': LOG_FILE_SIZE_LIMIT,
      #   'backupCount': LOG_BACKUP_COUNT,
      #   'formatter': 'standard',
      #},
   },
   'loggers': {
      #'django': {
      #   'handlers': ['django'],
      #   'level': 'DEBUG',
      #   'propagate': True,
      #},
      'balsam': {
         'handlers': ['default'],
         'level': 'DEBUG',
          'propagate': True,
      },
      'balsam.django_config': {
         'handlers': ['balsam-db-config'],
         'level': 'DEBUG',
          'propagate': False,
      },
      'balsam.service.models': {
         'handlers': ['balsam-db-config'],
         'level': 'DEBUG',
          'propagate': False,
      },
   }
}

import logging
logger = logging.getLogger(__name__)
def log_uncaught_exceptions(exctype, value, tb,logger=logger):
   logger.error(f"Uncaught Exception {exctype}: {value}",exc_info=(exctype,value,tb))
   logger = logging.getLogger('console')
   logger.error(f"Uncaught Exception {exctype}: {value}",exc_info=(exctype,value,tb))
sys.excepthook = log_uncaught_exceptions

# -----------------------
# SQLITE CLIENT SETUP
# ------------------------
is_server = os.environ.get('IS_BALSAM_SERVER')=='True'
is_daemon = os.environ.get('IS_SERVER_DAEMON')=='True'
using_sqlite = DATABASES['default']['ENGINE'].endswith('sqlite3')
SAVE_CLIENT = None

if using_sqlite and not (is_server or is_daemon):
    from balsam.django_config import sqlite_client
    SAVE_CLIENT = sqlite_client.Client(serverinfo.ServerInfo(BALSAM_PATH))
    if SAVE_CLIENT.serverAddr is None:
        SAVE_CLIENT = None


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '=gyp#o9ac0@w3&-^@a)j&f#_n-o=k%z2=g5u@z5+klmh_*hebj'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []

# Application definition

INSTALLED_APPS = [
    'balsam.service.apps.BalsamCoreConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE_CLASSES = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'balsam.django_config.urls'

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

WSGI_APPLICATION = 'balsam.django_config.wsgi.application'





# Password validation
# https://docs.djangoproject.com/en/1.9/ref/settings/#auth-password-validators

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


# Internationalization
# https://docs.djangoproject.com/en/1.9/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.9/howto/static-files/

STATIC_URL = '/static/'