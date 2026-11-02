import io
import tempfile
from unittest.mock import patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Analysis, Alert, AlertHistory

class WorkflowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.temp.name, INFERENCE_MODE='demo')
        self.settings_override.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user('operator', password='test-pass-123')
        self.other = get_user_model().objects.create_user('other', password='test-pass-456')
        self.client.force_login(self.user)

    def upload(self, smoke=True):
        file = io.BytesIO()
        Image.new('RGB', (200, 100), '#316c3c').save(file, 'PNG')
        file.seek(0)
        file.name = 'forest.png'
        data = {'image': file, 'source': 'Camera 01'}
        if smoke:
            data['simulate_smoke'] = 'on'
        return self.client.post(reverse('analyze'), data)

    def test_demo_workflow_and_audit(self):
        response = self.upload()
        run = Analysis.objects.get()
        self.assertRedirects(response, reverse('detail', args=[run.pk]))
        self.assertEqual(run.detections[0]['confidence'], None)
        alert = Alert.objects.get()
        url = reverse('transition', args=[alert.pk])
        self.client.post(url, {'status': 'CONFIRMED', 'note': 'Đã xem ảnh'})
        self.client.post(url, {'status': 'RESOLVED'})
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'RESOLVED')
        self.assertEqual(AlertHistory.objects.count(), 2)
        evidence = self.client.get(reverse('evidence', args=[run.pk, 'annotated']))
        self.assertEqual(evidence.status_code, 200)
        evidence.close()

    def test_negative_does_not_create_alert(self):
        self.upload(smoke=False)
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(Analysis.objects.get().detections, [])

    def test_other_user_cannot_read_evidence_or_update(self):
        self.upload()
        run = Analysis.objects.get()
        alert = Alert.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('detail', args=[run.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('evidence', args=[run.pk, 'original'])).status_code, 404)
        self.assertEqual(self.client.post(reverse('transition', args=[alert.pk]), {'status': 'CONFIRMED'}).status_code, 404)

    def test_invalid_transition_and_get_cannot_mutate(self):
        self.upload()
        alert = Alert.objects.get()
        url = reverse('transition', args=[alert.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'status': 'RESOLVED'})
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'PENDING')
        self.assertEqual(AlertHistory.objects.count(), 0)

    def test_non_image_rejected(self):
        invalid = io.BytesIO(b'<script>alert(1)</script>')
        invalid.name = 'forest.jpg'
        self.client.post(reverse('analyze'), {'image': invalid, 'source': 'Camera'})
        self.assertEqual(Analysis.objects.count(), 0)

    def test_missing_model_never_silently_simulates(self):
        with override_settings(INFERENCE_MODE='yolo', MODEL_PATH='missing-test.pt'):
            self.upload()
        self.assertEqual(Analysis.objects.count(), 0)

    def test_adapter_for_trained_model(self):
        class Scalar:
            def __init__(self, value): self.value = value
            def item(self): return self.value
        class Coordinates:
            def tolist(self): return [10, 10, 120, 80]
        class Box:
            cls = Scalar(0)
            conf = Scalar(.83)
            xyxy = [Coordinates()]
        class Result:
            names = {0: 'smoke'}
            boxes = [Box()]
        class Model:
            def predict(self, *args, **kwargs): return [Result()]
        with override_settings(INFERENCE_MODE='yolo'), patch('monitor.inference.load_model', return_value=Model()):
            self.upload()
        self.assertEqual(Analysis.objects.get().mode, 'yolo')
        self.assertEqual(Analysis.objects.get().detections[0]['confidence'], .83)
        self.assertEqual(Alert.objects.count(), 1)

    def test_pages_and_auth(self):
        for url in ['/', '/dashboard/', '/alerts/']:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get('/dashboard/').status_code, 302)

    def test_csrf_required_for_upload(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('analyze'), {}).status_code, 403)
