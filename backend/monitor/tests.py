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

    def test_login_form_and_frontend_assets(self):
        from django.contrib.staticfiles import finders
        self.client.logout()
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        response = self.client.post(reverse('login'), {
            'username': 'operator', 'password': 'test-pass-123',
        })
        self.assertRedirects(response, reverse('dashboard'))
        for asset in ('css/app.css', 'js/app.js', 'img/forest.jpg'):
            self.assertIsNotNone(finders.find(asset))

    def test_fosp_dispatch_preserves_mask_evidence_and_alert(self):
        overlay = Image.new('RGB', (200, 100), '#ffa52d')
        detections = [{'label': 'smoke', 'mask_coverage': 12.5}]
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
        self.assertRedirects(post_res, reverse('dashboard'))
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
            self.assertRedirects(res, reverse('dashboard'))
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
            self.assertRedirects(res2, reverse('dashboard'))
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
