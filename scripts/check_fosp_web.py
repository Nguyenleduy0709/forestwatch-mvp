"""Exercise real FoSp through Django without touching user data (not a CI test)."""
import argparse
import io
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--image', type=Path, required=True)
args = parser.parse_args()
if not args.image.is_file():
    parser.error('Image does not exist')
os.environ['DJANGO_SETTINGS_MODULE'] = 'forestwatch.settings'
os.environ['INFERENCE_MODE'] = 'fosp'
os.environ['DATABASE_PATH'] = ':memory:'

import django
django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, override_settings
from monitor.models import Analysis, Alert
from PIL import Image, ImageChops

call_command('migrate', verbosity=0)
user = get_user_model().objects.create_user('fosp-check')
client = Client()
client.force_login(user)
buffer = io.BytesIO()
with Image.open(args.image) as image:
    image.convert('RGB').save(buffer, format='PNG')
buffer.seek(0)
buffer.name = 'smoke-check.png'
temp_root = ROOT / 'work' / 'checks'
temp_root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=temp_root) as media:
    with override_settings(MEDIA_ROOT=media, ALLOWED_HOSTS=['testserver']):
        began = monotonic()
        response = client.post('/analyze/', {'source': 'FoSp GPU check', 'image': buffer})
        assert response.status_code == 302, response.status_code
        assert Analysis.objects.count() == 1, 'Upload failed: check model/runtime/CUDA'
        run = Analysis.objects.get()
        assert run.mode == 'fosp'
        assert Alert.objects.filter(analysis=run).exists() == bool(run.detections)
        page = client.get(response.url)
        assert page.status_code == 200 and b'FOSP' in page.content
        for kind in ('original', 'annotated'):
            evidence = client.get(f'/runs/{run.pk}/image/{kind}/')
            assert evidence.status_code == 200
            evidence.close()
        if run.detections:
            assert 0 < run.detections[0]['mask_coverage'] <= 100
            with Image.open(run.original.path) as original, Image.open(run.annotated.path) as annotated:
                assert ImageChops.difference(original, annotated).getbbox(), 'Missing overlay'
        client.logout()
        assert client.get(response.url).status_code == 302
        print('FOSP_WEB_CHECK_PASSED', run.detections, round(monotonic() - began, 2), flush=True)
