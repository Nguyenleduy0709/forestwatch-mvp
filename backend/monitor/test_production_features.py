import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .live_cameras import LiveCameraError, compute_fire_danger, validate_stream_host
from .models import Alert, Analysis, CameraStation, MonitoringProfile, NotificationLog, SmokeCapture
from .notifications import cleanup_old_captures


def _jpeg_upload(name='frame.jpg', size=(160, 120)):
    buf = io.BytesIO()
    Image.new('RGB', size, '#294836').save(buf, 'JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), 'image/jpeg')


class ProductionFeaturesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('enterprise-op', password='StrongPassword123!')
        cls.other = get_user_model().objects.create_user('other-op', password='StrongPassword456!')

    def setUp(self):
        self.client.force_login(self.owner)
        self.key = uuid4().hex

    def test_camera_station_api_crud_masks_rtsp_password_and_scopes_owner(self):
        res = self.client.post(
            reverse('stations_api'),
            data=json.dumps({
                'channel_number': 1,
                'name': 'Đồi Thông Bắc',
                'location_name': 'Tiểu khu 142',
                'source_type': 'rtsp',
                'stream_url': 'rtsp://admin:SuperSecret123@192.168.1.50:554/stream1',
                'latitude': 16.215,
                'longitude': 107.855,
                'azimuth': 90,
                'exclusion_zones': [{'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 0.28, 'label': 'Bầu trời'}],
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        st = res.json()['station']
        self.assertEqual(st['name'], 'Đồi Thông Bắc')
        self.assertEqual(st['stream_url'], 'rtsp://admin:****@192.168.1.50:554/stream1')
        self.assertEqual(len(st['exclusion_zones']), 1)

        # Other user cannot inspect or delete owner's station
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('station_detail_api', args=[st['id']])).status_code, 404)
        self.assertEqual(self.client.delete(reverse('station_detail_api', args=[st['id']])).status_code, 404)

    def test_simulator_live_frame_streaming_and_ssrf_protection(self):
        station = CameraStation.objects.create(
            owner=self.owner,
            channel_number=2,
            name='Cam Giả lập 02',
            source_type='simulator',
            simulator_scenario='smoke_rise',
        )
        url = reverse('live_camera_frame', args=[station.pk])
        frame = self.client.get(url)
        self.assertEqual(frame.status_code, 200)
        self.assertEqual(frame['Content-Type'], 'image/jpeg')
        self.assertEqual(self.client.get(url, headers={'If-None-Match': frame['ETag']}).status_code, 304)

        station.refresh_from_db()
        self.assertEqual(station.status, CameraStation.HealthStatus.ONLINE)
        self.assertIsNotNone(station.last_heartbeat_at)

        # Link-local metadata & loopback blocked for remote camera streams
        for host in ('127.0.0.1', '169.254.169.254'):
            with self.assertRaises(LiveCameraError):
                validate_stream_host(host)

    def test_anti_false_positive_persistence_coverage_and_roi_exclusion(self):
        profile = MonitoringProfile.for_user(self.owner)
        profile.min_confidence = 0.45
        profile.min_smoke_coverage = 0.50
        profile.persistence_frames = 3
        profile.save()

        station = CameraStation.objects.create(
            owner=self.owner,
            channel_number=3,
            source_key=uuid4(),
            name='Đỉnh Đèo',
            source_type='simulator',
            exclusion_zones=[{'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 0.30, 'label': 'Bầu trời'}],
        )

        def post_scan(detections):
            with patch('monitor.views.predict', return_value=(detections, Image.new('RGB', (160, 120), '#294836'))):
                with override_settings(INFERENCE_MODE='fosp'):
                    resp = self.client.post(reverse('scan_frame'), {
                        'source_key': station.source_key.hex,
                        'source_kind': 'simulator',
                        'station_id': str(station.pk),
                        'source': 'CAM 03 · Đỉnh Đèo',
                        'moving': '1',
                        'image': _jpeg_upload(),
                    })
            self.assertEqual(resp.status_code, 200)
            return resp.json()

        # 1. Tiny spot below min_smoke_coverage (0.20 < 0.50) ignored completely
        r_small = post_scan([{'label': 'smoke', 'confidence': 0.85, 'mask_coverage': 0.20, 'box': [40, 60, 80, 100]}])
        self.assertFalse(r_small['alert'])
        self.assertFalse(r_small['suspected'])

        # 2. Cloud false positive centered inside top exclusion ROI [y: 0..36 of 120] filtered out
        r_roi = post_scan([{'label': 'smoke', 'confidence': 0.90, 'mask_coverage': 3.5, 'box': [20, 5, 100, 25]}])
        self.assertFalse(r_roi['has_smoke'])
        self.assertFalse(r_roi['alert'])

        # 3. Real smoke outside ROI requires 3 consecutive frames before raising Alert
        real_det = [{'label': 'smoke', 'confidence': 0.85, 'mask_coverage': 2.5, 'box': [50, 60, 110, 110]}]
        s1 = post_scan(real_det)
        self.assertTrue(s1['suspected'])
        self.assertEqual(s1['streak'], 1)
        self.assertFalse(s1['alert'])
        self.assertEqual(Alert.objects.count(), 0)

        s2 = post_scan(real_det)
        self.assertTrue(s2['suspected'])
        self.assertEqual(s2['streak'], 2)
        self.assertEqual(Alert.objects.count(), 0)

        with patch('monitor.views.dispatch_alert_notifications') as dispatch_mock:
            s3 = post_scan(real_det)
        self.assertFalse(s3['suspected'])
        self.assertTrue(s3['alert'])
        self.assertTrue(s3['event_created'])
        self.assertEqual(Alert.objects.count(), 1)
        dispatch_mock.assert_called_once()
        station.refresh_from_db()
        self.assertEqual(station.status, CameraStation.HealthStatus.ALERTING)

    def test_preserve_replay_history_keeps_previous_captures_on_video_replay(self):
        profile = MonitoringProfile.for_user(self.owner)
        profile.preserve_replay_history = True
        profile.save()

        def scan_pos(pos):
            det = [{'label': 'smoke', 'confidence': 0.80, 'mask_coverage': 2.5, 'box': [20, 20, 80, 80]}]
            with patch('monitor.views.predict', return_value=(det, Image.new('RGB', (160, 120), '#294836'))):
                with override_settings(INFERENCE_MODE='fosp'):
                    return self.client.post(reverse('scan_frame'), {
                        'source_key': self.key,
                        'source_kind': 'video',
                        'source': 'CAM 01 · Video Test',
                        'moving': '1',
                        'position': str(pos),
                        'image': _jpeg_upload(),
                    })

        self.assertEqual(scan_pos(2.0).status_code, 200)
        self.assertEqual(scan_pos(8.0).status_code, 200)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 2)

        # Replay back to 0.5s should keep old captures when preserve_replay_history=True
        self.assertEqual(scan_pos(0.5).status_code, 200)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 3)

    def test_settings_enterprise_preset_and_multichannel_test_notifications(self):
        preset_res = self.client.post(reverse('settings'), {'action': 'preset_enterprise'})
        self.assertRedirects(preset_res, reverse('settings'))
        profile = MonitoringProfile.for_user(self.owner)
        self.assertEqual(profile.persistence_frames, 3)
        self.assertEqual(profile.min_smoke_coverage, 0.25)
        self.assertTrue(profile.preserve_replay_history)

        mock_resp = Mock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'), \
             patch('monitor.notifications.urllib.request.urlopen', return_value=mock_resp) as urlopen_mock:
            test_res = self.client.post(reverse('settings'), {
                'action': 'test_notify',
                'organization_name': 'Hạt Kiểm Lâm A',
                'min_confidence': '0.45',
                'min_smoke_coverage': '0.25',
                'persistence_frames': '3',
                'capture_interval_seconds': '3',
                'alert_cooldown_seconds': '60',
                'retention_days': '30',
                'preserve_replay_history': 'on',
                'telegram_enabled': 'on',
                'telegram_bot_token': '123456:TESTTOKEN',
                'telegram_chat_id': '-100999999',
                'email_enabled': 'on',
                'email_recipients': 'ranger@example.com',
                'webhook_enabled': 'on',
                'webhook_url': 'https://hooks.example.com/notify',
            })
        self.assertEqual(test_res.status_code, 200)
        self.assertEqual(urlopen_mock.call_count, 2)  # Telegram + Webhook
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(NotificationLog.objects.filter(owner=self.owner, status='SENT').count(), 3)

    def test_map_weather_csv_export_and_capture_cleanup(self):
        CameraStation.objects.create(
            owner=self.owner,
            channel_number=1,
            name='Trạm A',
            location_name='Rừng Bắc',
            latitude=16.25,
            longitude=107.82,
        )
        with patch('monitor.live_cameras.urllib.request.urlopen', side_effect=OSError('offline')):
            map_res = self.client.get(reverse('map'))
            self.assertEqual(map_res.status_code, 200)
            self.assertContains(map_res, 'data-center-lat="16.25"')
            self.assertContains(map_res, 'https://maps.google.com/?q=16.250000,107.820000')
            w_res = self.client.get(reverse('weather_risk_api'), {'lat': '16.25', 'lon': '107.82'})
            self.assertEqual(w_res.status_code, 200)
            self.assertIn('danger_level', w_res.json())

        _, lvl, _, _ = compute_fire_danger(38.0, 25.0, 30.0)
        self.assertEqual(lvl, 5)

        run_pending = Analysis.objects.create(owner=self.owner, source='CAM 01 · Trạm A', mode='fosp', detections=[{'confidence': 0.8, 'mask_coverage': 2.2}])
        al_pending = Alert.objects.create(analysis=run_pending, status=Alert.Status.PENDING)
        cap_old = SmokeCapture.objects.create(owner=self.owner, alert=al_pending, source='CAM 01 · Trạm A', original='old.jpg')
        SmokeCapture.objects.filter(pk=cap_old.pk).update(created_at=timezone.now() - timedelta(days=45))

        run_conf = Analysis.objects.create(owner=self.owner, source='CAM 01 · Trạm A', mode='fosp', detections=[{'confidence': 0.9, 'mask_coverage': 5.0}])
        al_conf = Alert.objects.create(analysis=run_conf, status=Alert.Status.CONFIRMED)
        cap_conf = SmokeCapture.objects.create(owner=self.owner, alert=al_conf, source='CAM 01 · Trạm A', original='conf.jpg')
        SmokeCapture.objects.filter(pk=cap_conf.pk).update(created_at=timezone.now() - timedelta(days=45))

        csv_res = self.client.get(reverse('export_alerts_csv'))
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn('CAM 01', csv_res.content.decode('utf-8-sig'))

        deleted = cleanup_old_captures(owner=self.owner, retention_days=30)
        self.assertEqual(deleted, 1)
        self.assertFalse(SmokeCapture.objects.filter(pk=cap_old.pk).exists())
        self.assertTrue(SmokeCapture.objects.filter(pk=cap_conf.pk).exists())
