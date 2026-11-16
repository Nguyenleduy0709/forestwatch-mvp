from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _configure_sqlite_wal(sender, connection, **kwargs):
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA busy_timeout=15000;')
        db_name = str(connection.settings_dict.get('NAME') or '')
        if db_name and db_name != ':memory:' and not db_name.startswith('file::memory:'):
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')


class MonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitor'

    def ready(self):
        connection_created.connect(_configure_sqlite_wal)
