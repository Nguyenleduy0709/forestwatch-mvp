"""Check the demo through real HTTP using temporary database and media."""
import http.cookiejar
import io
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

root = Path(__file__).resolve().parent.parent
(root / 'work/checks').mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root / 'backend'))
from PIL import Image
with tempfile.TemporaryDirectory(dir=root / 'work/checks') as scratch:
    os.environ.update(DJANGO_SETTINGS_MODULE='forestwatch.settings', INFERENCE_MODE='demo',
        DATABASE_PATH=str(Path(scratch) / 'http.sqlite3'), MEDIA_ROOT=str(Path(scratch) / 'media'),
        DJANGO_ALLOWED_HOSTS='127.0.0.1', DJANGO_DEBUG='1')
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from monitor.models import Analysis, Alert, AlertHistory
    call_command('migrate', verbosity=0)
    password = secrets.token_urlsafe(24)
    get_user_model().objects.create_user('http-check', password=password)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (Path(scratch) / 'server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, 'backend/manage.py', 'runserver', f'127.0.0.1:{port}', '--noreload'], cwd=root, stdout=log, stderr=log)
        try:
            base = f'http://127.0.0.1:{port}'
            cookies = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
            def get(path):
                response = opener.open(base + path, timeout=20)
                assert response.status == 200
                return response.read()
            for _ in range(100):
                try:
                    assert b'ForestWatch' in get('/')
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(.1)
            else:
                raise AssertionError('Server failed to start')
            for path in ['/static/css/app.css', '/static/js/app.js', '/static/img/forest.jpg']:
                assert get(path)
            get('/login/')
            def csrf(): return next(cookie.value for cookie in cookies if cookie.name == 'csrftoken')
            def post(path, data):
                data['csrfmiddlewaretoken'] = csrf()
                request = urllib.request.Request(base + path, data=urllib.parse.urlencode(data).encode())
                return opener.open(request, timeout=20)
            assert post('/login/', {'username':'http-check', 'password':password}).url.endswith('/control-room/')
            image = io.BytesIO()
            Image.new('RGB', (200, 100), '#316c3c').save(image, 'PNG')
            def upload(smoke):
                boundary = 'ForestWatch' + secrets.token_hex(8)
                fields = {'csrfmiddlewaretoken':csrf(), 'source':'HTTP smoke check'}
                if smoke: fields['simulate_smoke'] = 'on'
                payload = b''
                for key,value in fields.items():
                    payload += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                payload += f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="forest.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
                payload += image.getvalue() + f'\r\n--{boundary}--\r\n'.encode()
                request = urllib.request.Request(base + '/analyze/', data=payload, headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
                response = opener.open(request, timeout=20)
                assert '/runs/' in response.url
                return response.url, response.read()
            url, page = upload(True)
            run = Analysis.objects.get()
            assert run.mode == 'demo' and Alert.objects.count() == 1
            for kind in ['original','annotated']:
                assert get(f'/runs/{run.pk}/image/{kind}/').startswith(b'\xff\xd8')
            alert = Alert.objects.get()
            for status in ['CONFIRMED','RESOLVED']:
                post(f'/alerts/{alert.pk}/transition/', {'status':status, 'note':'HTTP check'})
            alert.refresh_from_db()
            assert alert.status == 'RESOLVED' and AlertHistory.objects.count() == 2
            get('/alerts/?status=RESOLVED')
            upload(False)
            assert Analysis.objects.count() == 2 and Alert.objects.count() == 1
            post('/logout/', {})
            response = opener.open(base + f'/runs/{run.pk}/', timeout=20)
            assert '/login/' in response.url
            print('HTTP_DEMO_CHECK_PASSED: login, dashboard, static, upload +/-, original/annotated, alert transitions, logout')
        finally:
            server.terminate()
            server.wait(timeout=20)
            from django.db import connections
            connections.close_all()
