import base64
import io
import json
import tempfile
from types import ModuleType
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

    def test_login_form_and_frontend_assets(self):
        from django.contrib.staticfiles import finders
        self.client.logout()
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        response = self.client.post(reverse('login'), {
            'username': 'operator', 'password': 'test-pass-123',
        })
        self.assertRedirects(response, reverse('control_room'))
        for asset in ('css/app.css', 'js/app.js', 'img/forest.jpg'):
            self.assertIsNotNone(finders.find(asset))

    def test_fosp_dispatch_preserves_mask_evidence_and_alert(self):
        overlay = Image.new('RGB', (200, 100), '#ffa52d')
        detections = [{'label': 'smoke', 'mask_coverage': 12.5, 'confidence': 0.8}]
        with override_settings(INFERENCE_MODE='fosp'), patch(
            'monitor.fosp.predict_fosp', return_value=(detections, overlay)
        ):
            response = self.upload(smoke=False)
        run = Analysis.objects.get()
        self.assertEqual(run.mode, 'fosp')
        self.assertEqual(run.detections, detections)
        self.assertTrue(Alert.objects.filter(analysis=run).exists())
        self.assertContains(self.client.get(response.url), '12,50')
        with Image.open(run.annotated.path) as image:
            self.assertEqual(image.size, overlay.size)
            self.assertNotEqual(image.getpixel((0, 0)), (49, 108, 60))

    def test_fosp_missing_checkpoint_does_not_create_analysis_or_alert(self):
        from pathlib import Path
        from .fosp import _load_model
        _load_model.cache_clear()
        with override_settings(INFERENCE_MODE='fosp', FOSP_MODEL_PATH=Path(self.temp.name) / 'missing.pth'):
            response = self.upload()
        self.assertRedirects(response, reverse('dashboard'))
        self.assertEqual(Analysis.objects.count(), 0)
        self.assertEqual(Alert.objects.count(), 0)

    def test_registration_success_and_login(self):
        self.client.logout()
        res = self.client.get(reverse('register'))
        self.assertEqual(res.status_code, 200)
        post_res = self.client.post(reverse('register'), {
            'username': 'ranger_nam',
            'email': 'nam@forestwatch.vn',
            'password': 'safe-password-999',
            'password_confirm': 'safe-password-999',
        })
        self.assertRedirects(post_res, reverse('control_room'))
        user = get_user_model().objects.get(username='ranger_nam')
        self.assertEqual(user.email, 'nam@forestwatch.vn')
        # Check authenticated session
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_registration_validation_errors(self):
        self.client.logout()
        # Mismatched passwords
        res1 = self.client.post(reverse('register'), {
            'username': 'ranger_err',
            'email': 'err@forestwatch.vn',
            'password': 'safe-password-1',
            'password_confirm': 'different-pass-2',
        })
        self.assertEqual(res1.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='ranger_err').exists())

        # Duplicate username
        res2 = self.client.post(reverse('register'), {
            'username': 'operator',
            'email': 'unique@forestwatch.vn',
            'password': 'safe-password-1',
            'password_confirm': 'safe-password-1',
        })
        self.assertEqual(res2.status_code, 200)
        self.assertContains(res2, 'Tên đăng nhập này đã được sử dụng')

    def test_google_login_unconfigured_flow(self):
        self.client.logout()
        with override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET=''):
            res = self.client.get(reverse('google_login'))
            self.assertRedirects(res, reverse('login'))

    def test_google_login_redirect_when_configured(self):
        self.client.logout()
        with override_settings(
            GOOGLE_CLIENT_ID='test-client-id.apps.googleusercontent.com',
            GOOGLE_CLIENT_SECRET='test-secret-key',
        ):
            res = self.client.get(reverse('google_login'))
            self.assertEqual(res.status_code, 302)
            self.assertTrue(res.url.startswith('https://accounts.google.com/o/oauth2/v2/auth'))
            self.assertIn('client_id=test-client-id.apps.googleusercontent.com', res.url)
            self.assertIn('state=', res.url)
            self.assertTrue(self.client.session.get('google_oauth_state'))

    def test_google_callback_success_new_and_existing_user(self):
        self.client.logout()
        # 1. New user through Google OAuth
        session = self.client.session
        session['google_oauth_state'] = 'valid-test-state'
        session.save()

        mock_tokens = {'access_token': 'fake-access-token-123'}
        mock_userinfo = {
            'sub': 'google-uid-1001',
            'email': 'newranger@gmail.com',
            'given_name': 'Lam',
            'family_name': 'Nguyen',
        }

        with patch('monitor.auth_views.exchange_code_for_tokens', return_value=mock_tokens), \
             patch('monitor.auth_views.fetch_google_user_info', return_value=mock_userinfo):
            res = self.client.get(reverse('google_callback') + '?code=auth-code-xyz&state=valid-test-state')
            self.assertRedirects(res, reverse('control_room'))
            new_user = get_user_model().objects.get(email='newranger@gmail.com')
            self.assertEqual(new_user.first_name, 'Lam')
            self.assertEqual(new_user.last_name, 'Nguyen')
            self.assertEqual(int(self.client.session['_auth_user_id']), new_user.pk)

        # 2. Existing user logging in via Google OAuth
        self.client.logout()
        session = self.client.session
        session['google_oauth_state'] = 'second-test-state'
        session.save()

        with patch('monitor.auth_views.exchange_code_for_tokens', return_value=mock_tokens), \
             patch('monitor.auth_views.fetch_google_user_info', return_value=mock_userinfo):
            res2 = self.client.get(reverse('google_callback') + '?code=auth-code-xyz&state=second-test-state')
            self.assertRedirects(res2, reverse('control_room'))
            self.assertEqual(int(self.client.session['_auth_user_id']), new_user.pk)

    def test_google_callback_invalid_state_and_error(self):
        self.client.logout()
        # State mismatch
        session = self.client.session
        session['google_oauth_state'] = 'correct-state'
        session.save()
        res = self.client.get(reverse('google_callback') + '?code=code123&state=wrong-state')
        self.assertRedirects(res, reverse('login'))

        # Error returned by Google
        res2 = self.client.get(reverse('google_callback') + '?error=access_denied')
        self.assertRedirects(res2, reverse('login'))

    def test_logout_view(self):
        self.client.force_login(self.user)
        res = self.client.post(reverse('logout'))
        self.assertRedirects(res, reverse('home'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_upload_via_image_url(self):
        buf = io.BytesIO()
        Image.new('RGB', (160, 120), '#2d7a42').save(buf, 'JPEG')
        sample_bytes = buf.getvalue()

        with patch('monitor.forms.fetch_image_from_url', return_value=sample_bytes):
            res = self.client.post(reverse('analyze'), {
                'image_url': 'https://drive.google.com/file/d/test12345/view?usp=sharing',
                'source': 'Drive Camera',
                'simulate_smoke': 'on',
            })
            self.assertEqual(res.status_code, 302)
            run = Analysis.objects.filter(source='Drive Camera').first()
            self.assertIsNotNone(run)
            labels = [d['label'] for d in run.detections]
            self.assertIn('smoke', labels)

    def test_detail_view_includes_quick_upload_form(self):
        self.upload()
        run = Analysis.objects.first()
        res = self.client.get(reverse('detail', args=[run.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertIn('quick-upload-form', res.content.decode('utf-8'))
        self.assertIn('split-image-view', res.content.decode('utf-8'))

    def test_control_room_view(self):
        res = self.client.get(reverse('control_room'))
        self.assertEqual(res.status_code, 200)
        self.assertIn('cctv-control-center', res.content.decode('utf-8'))
        self.assertIn('cctv-camera-grid', res.content.decode('utf-8'))

    def test_scan_frame_api(self):
        buf = io.BytesIO()
        Image.new('RGB', (160, 120), '#316c3c').save(buf, 'JPEG')
        b64 = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('utf-8')

        res = self.client.post(reverse('scan_frame'), {
            'image_b64': b64,
            'source': 'CAM 01 - Trạm Cột 1',
            'simulate_smoke': '1',
            'save_alert': '1',
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')
        self.assertTrue(data['has_smoke'])
        self.assertTrue(data['alert'])
        self.assertIsNotNone(data['alert_id'])

    def test_extract_youtube_video_id(self):
        from monitor.forms import extract_youtube_video_id
        self.assertEqual(extract_youtube_video_id('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        self.assertEqual(extract_youtube_video_id('https://youtu.be/dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        self.assertEqual(extract_youtube_video_id('https://www.youtube.com/shorts/dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        self.assertEqual(extract_youtube_video_id('https://www.youtube.com/embed/dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        self.assertEqual(extract_youtube_video_id('https://www.youtube.com/live/dQw4w9WgXcQ'), 'dQw4w9WgXcQ')
        self.assertIsNone(extract_youtube_video_id('https://google.com/test'))

    def test_scan_frame_api_with_image_url(self):
        buf = io.BytesIO()
        Image.new('RGB', (160, 120), '#316c3c').save(buf, 'JPEG')
        sample_bytes = buf.getvalue()

        with patch('monitor.views.fetch_image_from_url', return_value=sample_bytes):
            res = self.client.post(reverse('scan_frame'), {
                'image_url': 'https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
                'source': 'CAM 02 - YouTube Stream',
                'simulate_smoke': '1',
                'save_alert': '1',
            })
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data['status'], 'success')
            self.assertTrue(data['has_smoke'])
            self.assertTrue(data['alert'])

    def test_alert_list_renders_if_delete_route_is_unavailable(self):
        from forestwatch.urls import urlpatterns

        self.upload()
        urlconf = ModuleType('alerts_without_delete')
        urlconf.urlpatterns = [route for route in urlpatterns if getattr(route, 'name', None) != 'delete_alerts']
        with self.settings(ROOT_URLCONF=urlconf):
            response = self.client.get('/alerts/')
        self.assertContains(response, 'Camera 01')
        self.assertNotContains(response, 'id="batch-delete-form"')

    def test_delete_selected_alerts(self):
        # Create 2 alerts for user
        self.upload()
        alert1 = Alert.objects.get()
        self.upload()
        alert2 = Alert.objects.exclude(pk=alert1.pk).get()
        self.assertEqual(Alert.objects.count(), 2)

        # Delete alert1 via POST alert_ids
        res = self.client.post(reverse('delete_alerts'), {'alert_ids': [str(alert1.pk)]})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Alert.objects.count(), 1)
        self.assertFalse(Alert.objects.filter(pk=alert1.pk).exists())
        self.assertTrue(Alert.objects.filter(pk=alert2.pk).exists())

    def test_delete_single_alert_json(self):
        self.upload()
        alert = Alert.objects.get()
        res = self.client.post(reverse('delete_alerts'),
                               json.dumps({'alert_id': str(alert.pk)}),
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['success'])
        self.assertEqual(res.json()['deleted_count'], 1)
        self.assertEqual(Alert.objects.count(), 0)

    def test_cannot_delete_other_user_alert(self):
        self.upload()
        alert = Alert.objects.get()
        self.client.force_login(self.other)
        res = self.client.post(reverse('delete_alerts'), {'alert_ids': [str(alert.pk)]})
        self.assertEqual(res.status_code, 302)
        # Alert should still exist because it belongs to self.user, not self.other
        self.assertEqual(Alert.objects.count(), 1)
