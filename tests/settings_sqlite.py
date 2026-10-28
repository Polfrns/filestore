"""Local contract tests without PostgreSQL; production settings are unchanged."""
from leedeo_filestore.settings import *  # noqa: F403
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
