import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .live_cameras import LiveCameraError, validate_stream_host
from .models import Alert, Analysis, CameraStation, MonitoringProfile, NotificationLog, SmokeCapture
from .notifications import cleanup_old_captures, dispatch_alert_notifications


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
                'source_type': 'rtsp',
                'stream_url': 'rtsp://admin:SuperSecret123@192.168.1.50:554/stream1',
                'exclusion_zones': [{'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 0.28, 'label': 'Bầu trời'}],
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        st = res.json()['station']
        self.assertEqual(st['name'], 'Đồi Thông Bắc')
        self.assertEqual(st['stream_url'], 'rtsp://admin:****@192.168.1.50:554/stream1')
        self.assertEqual(len(st['exclusion_zones']), 1)
        for forbidden_key in ('location_name', 'latitude', 'longitude', 'azimuth', 'fov_degrees', 'range_km', 'maps_url', 'simulator_scenario'):
            self.assertNotIn(forbidden_key, st)

        # Reject legacy source types not present in the UI
        for legacy_type in ('phone', 'simulator', 'webcam', 'http_snapshot'):
            bad = self.client.post(
                reverse('stations_api'),
                data=json.dumps({'channel_number': 2, 'name': 'Legacy', 'source_type': legacy_type}),
                content_type='application/json',
            )
            self.assertEqual(bad.status_code, 400)

        # Other user cannot inspect or delete owner's station
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('station_detail_api', args=[st['id']])).status_code, 404)
        self.assertEqual(self.client.delete(reverse('station_detail_api', args=[st['id']])).status_code, 404)

    def test_rtsp_live_frame_streaming_and_ssrf_protection(self):
        station = CameraStation.objects.create(
            owner=self.owner,
            channel_number=2,
            name='Cam RTSP 02',
            source_type='rtsp',
            stream_url='rtsp://user:pass@192.168.1.60:554/stream1',
        )
        buf = io.BytesIO()
        Image.new('RGB', (160, 120), '#294836').save(buf, 'JPEG')
        url = reverse('live_camera_frame', args=[station.pk])
        with patch('monitor.live_cameras.fetch_rtsp_snapshot', return_value=buf.getvalue()):
            frame = self.client.get(url)
        self.assertEqual(frame.status_code, 200)
        self.assertEqual(frame['Content-Type'], 'image/jpeg')
        with patch('monitor.live_cameras.fetch_rtsp_snapshot', return_value=buf.getvalue()):
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
            source_type='rtsp',
            stream_url='rtsp://admin:pass@192.168.1.70:554/stream1',
            exclusion_zones=[{'x': 0.0, 'y': 0.0, 'w': 1.0, 'h': 0.30, 'label': 'Bầu trời'}],
        )

        # Reject legacy source_kind in scan_frame API
        bad_kind = self.client.post(reverse('scan_frame'), {
            'source_key': station.source_key.hex,
            'source_kind': 'phone',
            'image': _jpeg_upload(),
        })
        self.assertEqual(bad_kind.status_code, 400)

        def post_scan(detections):
            with patch('monitor.views.predict', return_value=(detections, Image.new('RGB', (160, 120), '#294836'))):
                with override_settings(INFERENCE_MODE='fosp'):
                    resp = self.client.post(reverse('scan_frame'), {
                        'source_key': station.source_key.hex,
                        'source_kind': 'rtsp',
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
            })
        self.assertEqual(test_res.status_code, 200)
        self.assertEqual(urlopen_mock.call_count, 1)  # Telegram only
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(NotificationLog.objects.filter(owner=self.owner, status='SENT').count(), 2)

    def test_dispatch_alert_omits_location_and_map_links(self):
        profile = MonitoringProfile.for_user(self.owner)
        profile.telegram_enabled = True
        profile.telegram_bot_token = '123456:TESTTOKEN'
        profile.telegram_chat_id = '-100999999'
        profile.email_enabled = True
        profile.email_recipients = 'ranger@example.com'
        profile.save()

        station = CameraStation.objects.create(
            owner=self.owner,
            channel_number=1,
            name='Trạm Chòi Canh Bắc',
            source_type='url',
        )
        run = Analysis.objects.create(
            owner=self.owner,
            station=station,
            source='CAM 01 · Trạm Chòi Canh Bắc',
            mode='fosp',
            detections=[{'confidence': 0.803, 'mask_coverage': 2.1}],
        )
        alert = Alert.objects.create(analysis=run)

        captured_requests = []

        def fake_urlopen(req, timeout=8):
            captured_requests.append((req.full_url, req.data.decode('utf-8', errors='replace')))
            resp = Mock()
            resp.status = 200
            resp.read.return_value = b'{"ok": true}'
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp

        with override_settings(
            EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
            PUBLIC_BASE_URL='https://desktop-gfkd7bi.tailf86836.ts.net',
        ), patch('monitor.notifications.urllib.request.urlopen', side_effect=fake_urlopen):
            dispatch_alert_notifications(
                alert,
                station=station,
                base_url='http://127.0.0.1:8000/',
                source_key='cam1',
                sync=True,
            )

        tg_raw = next(body for url, body in captured_requests if 'api.telegram.org' in url)
        tg_text = json.loads(tg_raw)['text']
        self.assertIn('CAM 01 · Trạm Chòi Canh Bắc', tg_text)
        self.assertIn(f'https://desktop-gfkd7bi.tailf86836.ts.net/runs/{run.id}/', tg_text)
        self.assertNotIn('127.0.0.1:8000', tg_text)
        self.assertNotIn('Vị trí', tg_text)
        self.assertNotIn('Bản đồ', tg_text)
        self.assertNotIn('maps.google.com', tg_text)
        self.assertNotIn('Tiểu khu 142', tg_text)

        self.assertEqual(len(mail.outbox), 1)
        email_msg = mail.outbox[0]
        self.assertNotIn('Bản đồ', email_msg.body)
        self.assertNotIn('maps.google.com', email_msg.body)
        html_alt = email_msg.alternatives[0][0]
        self.assertNotIn('Vị trí bản đồ', html_alt)
        self.assertNotIn('maps.google.com', html_alt)

    def test_csv_export_and_capture_cleanup(self):
        CameraStation.objects.create(
            owner=self.owner,
            channel_number=1,
            name='Trạm A',
            source_type='url',
        )

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
        csv_text = csv_res.content.decode('utf-8-sig')
        self.assertIn('CAM 01', csv_text)
        self.assertNotIn('Tọa độ GPS', csv_text)

        deleted = cleanup_old_captures(owner=self.owner, retention_days=30)
        self.assertEqual(deleted, 1)
        self.assertFalse(SmokeCapture.objects.filter(pk=cap_old.pk).exists())
        self.assertTrue(SmokeCapture.objects.filter(pk=cap_conf.pk).exists())

    @override_settings(
        TELEGRAM_BOT_TOKEN='123456:SYSBOTTOKEN',
        TELEGRAM_BOT_USERNAME='forestwatch_test_bot',
        PUBLIC_BASE_URL='https://desktop-gfkd7bi.tailf86836.ts.net',
    )
    def test_system_bot_one_click_pairing_and_dispatch(self):
        cache.clear()
        start_res = self.client.post(
            reverse('telegram_pair_start_api'),
            data=json.dumps({'mode': 'auto'}),
            content_type='application/json',
        )
        self.assertEqual(start_res.status_code, 200)
        start_data = start_res.json()
        code = start_data['code']
        self.assertTrue(code.startswith('fw_'))
        self.assertEqual(start_data['web_url'], 'https://web.telegram.org/a/#123456')
        self.assertEqual(start_data['personal_url'], f'https://t.me/forestwatch_test_bot?start={code}')
        self.assertEqual(start_data['group_url'], f'https://t.me/forestwatch_test_bot?startgroup={code}')

        # Other user must never inspect or hijack owner's pairing code
        self.client.force_login(self.other)
        hijack = self.client.get(reverse('telegram_pair_status_api'), {'code': code})
        self.assertEqual(hijack.status_code, 410)
        self.client.force_login(self.owner)

        sent_calls = []

        def fake_telegram_urlopen(req, timeout=15):
            url = req.full_url
            raw_body = req.data.decode('utf-8', errors='replace') if req.data else ''
            sent_calls.append((url, raw_body))
            resp = Mock()
            resp.status = 200
            if '/getUpdates' in url:
                payload = {
                    'ok': True,
                    'result': [
                        {
                            'update_id': 901,
                            'message': {
                                'message_id': 1,
                                'chat': {'id': -100888777, 'type': 'supergroup', 'title': 'PCCC & Kiểm lâm <Đội 1>'},
                                'text': f'/start@forestwatch_test_bot {code}',
                            },
                        },
                    ],
                }
                resp.read.return_value = json.dumps(payload).encode('utf-8')
            else:
                resp.read.return_value = b'{"ok": true}'
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp

        with patch('monitor.telegram_bot.urllib.request.urlopen', side_effect=fake_telegram_urlopen):
            status_res = self.client.get(reverse('telegram_pair_status_api'), {'code': code})
            self.assertEqual(status_res.status_code, 200)
            status_data = status_res.json()
            self.assertTrue(status_data['paired'])
            self.assertEqual(status_data['chat_id'], '-100888777')

            profile = MonitoringProfile.for_user(self.owner)
            self.assertTrue(profile.telegram_enabled)
            self.assertEqual(profile.telegram_chat_id, '-100888777')
            self.assertEqual(profile.telegram_bot_token, '')  # Uses shared system bot cleanly

            # HTML-sensitive group title must be escaped in confirmation sendMessage
            confirm_url, confirm_body = next((u, b) for u, b in sent_calls if '/sendMessage' in u)
            self.assertIn('123456:SYSBOTTOKEN', confirm_url)
            self.assertIn('PCCC &amp; Kiểm lâm &lt;Đội 1&gt;', json.loads(confirm_body)['text'])

            # Dispatch alert without per-user token should fallback seamlessly to system TELEGRAM_BOT_TOKEN
            run = Analysis.objects.create(
                owner=self.owner,
                source='CAM 02 · Đồi Thông',
                mode='fosp',
                detections=[{'confidence': 0.91, 'mask_coverage': 3.4}],
            )
            alert = Alert.objects.create(analysis=run)
            dispatch_alert_notifications(alert, source_key='cam2', sync=True)

            alert_posts = [u for u, _ in sent_calls if 'api.telegram.org/bot123456:SYSBOTTOKEN/send' in u]
            self.assertGreaterEqual(len(alert_posts), 2)

    @override_settings(TELEGRAM_BOT_TOKEN='123456:SYSBOTTOKEN', TELEGRAM_BOT_USERNAME='forestwatch_test_bot')
    def test_custom_bot_chat_discovery_blocks_system_bot_and_returns_private_chats(self):
        # Never expose shared system bot's update list across accounts
        blocked = self.client.post(
            reverse('telegram_discover_chats_api'),
            data=json.dumps({'bot_token': '123456:SYSBOTTOKEN'}),
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 400)

        def fake_discover(req, timeout=15):
            resp = Mock()
            resp.status = 200
            resp.read.return_value = json.dumps({
                'ok': True,
                'result': [
                    {
                        'update_id': 1,
                        'message': {'chat': {'id': 555111, 'type': 'private', 'first_name': 'Hieu', 'username': 'nhuhieu'}},
                    },
                    {
                        'update_id': 2,
                        'message': {'chat': {'id': -100444333, 'type': 'supergroup', 'title': 'Nhóm Trực Ban'}},
                    },
                ],
            }).encode('utf-8')
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp

        with patch('monitor.telegram_bot.urllib.request.urlopen', side_effect=fake_discover):
            res = self.client.post(
                reverse('telegram_discover_chats_api'),
                data=json.dumps({'bot_token': '999999:PRIVATEBOT'}),
                content_type='application/json',
            )
        self.assertEqual(res.status_code, 200)
        chats = res.json()['chats']
        self.assertEqual(len(chats), 2)
        self.assertEqual(chats[0]['chat_id'], '-100444333')
        self.assertEqual(chats[1]['chat_id'], '555111')
