#!/usr/bin/env python
import os
import sys

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forestwatch.settings')
    from django.core.management import execute_from_command_line
    from django.utils.autoreload import DJANGO_AUTORELOAD_ENV

    if sys.argv[1:2] == ['runserver'] and (
        '--noreload' in sys.argv or os.environ.get(DJANGO_AUTORELOAD_ENV) == 'true'
    ):
        from monitor.fosp import preload_torch_on_windows
        preload_torch_on_windows()

    execute_from_command_line(sys.argv)
