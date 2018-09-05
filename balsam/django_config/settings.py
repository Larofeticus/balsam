"""
Django settings for argobalsam project.

Generated by 'django-admin startproject' using Django 1.9.1.

For more information on this file, see
https://docs.djangoproject.com/en/1.9/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.9/ref/settings/
"""

from getpass import getuser
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

    user_settings_path = os.path.join(BALSAM_HOME, 'settings.json')
    user_policy_path = os.path.join(BALSAM_HOME, 'theta_policy.ini')
    user_templates_path = os.path.join(BALSAM_HOME, 'templates')
    here = os.path.dirname(os.path.abspath(__file__))
    if not os.path.exists(user_settings_path):
        default_settings_path = os.path.join(here, 'default_settings.json')
        shutil.copy(default_settings_path, user_settings_path)
        print("Set up your Balsam config directory at", BALSAM_HOME)
    if not os.path.exists(user_policy_path):
        default_policy_path = os.path.join(here, 'theta_policy.ini')
        shutil.copy(default_policy_path, user_policy_path)
    if not os.path.exists(user_templates_path):
        default_templates_path = os.path.join(here, 'templates')
        shutil.copytree(default_templates_path, user_templates_path)

    thismodule = sys.modules[__name__]
    user_settings = json.load(open(user_settings_path))
    for k, v in user_settings.items():
        setattr(thismodule, k, v)

# ---------------
# DATABASE SETUP
# ---------------
def resolve_db_path():
    if os.environ.get('BALSAM_DB_PATH'):
        path = os.environ['BALSAM_DB_PATH']
        path = os.path.expanduser(path)
        path = os.path.abspath(path)
    else:
        path = default_db_path

    if not os.path.exists(path):
        sys.stderr.write(f"balsamDB path {path} does not exist!\n")
        sys.stderr.write(f"Please use `source balsamactivate` to set BALSAM_DB_PATH to a valid location\n")
        sys.exit(1)

    os.environ['BALSAM_DB_PATH'] = path
    return path

def configure_db_backend(db_path):
    ENGINES = {
        'postgres': 'django.db.backends.postgresql_psycopg2',
    }
    NAMES = {
        'postgres': 'balsam',
    }
    OPTIONS = {
        'postgres' : {'connect_timeout' : 5},
    }

    info = serverinfo.ServerInfo(db_path)
    db_type = info['db_type']
    user = getuser()
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
BALSAM_PATH = resolve_db_path()
DATABASES = configure_db_backend(BALSAM_PATH)

pg_db_path = os.path.join(BALSAM_PATH, 'balsamdb')
if not os.path.exists(pg_db_path) and 'BALSAM_BOOTSTRAP' not in os.environ:
    print("Bootstrapping Postgres DB at", pg_db_path, "(this can take a minute)...")
    here = os.path.dirname(os.path.abspath(__file__))
    initpath = os.path.join(os.path.dirname(here), 'scripts', 'init.py')
    cmd = f"{sys.executable} {initpath}"
    proc = subprocess.run(f'BALSAM_BOOTSTRAP=1 {cmd} {BALSAM_PATH}', shell=True,
                   check=False,)# stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError
    else:
        refresh_db_index()
        print(f"\nSuccessfully created Balsam DB at: {BALSAM_PATH}")
        print(f"Use `source balsamactivate {os.path.basename(BALSAM_PATH)}` to begin working.\n")

# --------------------
# SUBDIRECTORY SETUP
# --------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGGING_DIRECTORY = os.path.join(BALSAM_PATH , 'log') 
DATA_PATH = os.path.join(BALSAM_PATH ,'data')
SERVICE_PATH = os.path.join(BALSAM_PATH ,'qsubmit')
BALSAM_WORK_DIRECTORY = DATA_PATH

for d in [
      BALSAM_PATH ,
      DATA_PATH,
      LOGGING_DIRECTORY,
      SERVICE_PATH
]:
    if not os.path.exists(d):
        os.makedirs(d)

# ----------------
# LOGGING SETUP
# ----------------
HANDLER_FILE = os.path.join(LOGGING_DIRECTORY, "balsam.log")
BALSAM_DB_CONFIG_LOG = os.path.join(LOGGING_DIRECTORY, "db.log")
BALSAM_SERVICE_LOG = os.path.join(LOGGING_DIRECTORY, "service.log")
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
      'balsam-service': {
         'level':LOG_HANDLER_LEVEL,
         'class':'logging.handlers.RotatingFileHandler',
         'filename': BALSAM_SERVICE_LOG,
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
      'balsam.service': {
         'handlers': ['balsam-service'],
         'level': 'DEBUG',
          'propagate': False,
      },
   }
}

import logging
from django.db import OperationalError

def log_uncaught_exceptions(exctype, value, tb):
    logger = logging.getLogger(__name__)
    logger.error(f"Uncaught Exception {exctype}: {value}",exc_info=(exctype,value,tb))
    for handler in logger.handlers: handler.flush()

    if isinstance(value, OperationalError):
        db_path = os.environ.get('BALSAM_DB_PATH')
        if not DATABASES['default']['PORT']:
            print("Balsam OperationalError: No DB is currently active")
            print("Please use `source balsamactivate` to activate a Balsam DB")
            print("Use `balsam which --list` for a listing of known DB names")
        else:
            print("Failed to reach the Balsam DB server at", db_path, f"(use 'balsam log db' for detailed traceback)")
    else:
        logger = logging.getLogger('console')
        logger.error(f"Uncaught Exception",exc_info=(exctype,value,tb))
        [h.flush() for h in logger.handlers]

sys.excepthook = log_uncaught_exceptions


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
