import os
from django.core.wsgi import get_wsgi_application
from monitor.fosp import preload_torch_on_windows

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forestwatch.settings')
preload_torch_on_windows()
application = get_wsgi_application()
