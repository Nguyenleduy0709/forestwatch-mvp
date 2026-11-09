from io import BytesIO
from ipaddress import IPv4Network
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .phone_camera import fetch_snapshot


@override_settings(PHONE_CAMERA_NETWORKS=(IPv4Network('192.168.10.0/24'),))
class PhoneCameraTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('phone-owner')
        cls.other = get_user_model().objects.create_user('phone-other')

    def setUp(self):
        self.client.force_login(self.owner)
        self.resolve = reverse('resolve_phone_camera')

    def test_frames_require_the_account_and_skip_identical_jpegs(self):
        reply = self.client.post(self.resolve, {'url': 'http://192.168.10.22:8080'})
        self.assertEqual(reply.status_code, 200)
        url = reply.json()['url']
        buffer = BytesIO()
        Image.new('RGB', (64, 40)).save(buffer, 'JPEG')
        with patch('monitor.phone_camera.fetch_snapshot', return_value=buffer.getvalue()) as fetch:
            frame = self.client.get(url)
            self.assertEqual(frame['Content-Type'], 'image/jpeg')
            self.assertEqual(frame['Cache-Control'], 'private, no-store')
            fetch.assert_called_once_with('192.168.10.22', '/shot.jpg')
            self.assertEqual(self.client.get(url, headers={'If-None-Match': frame['ETag']}).status_code, 304)
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(fetch.call_count, 2)

    def test_rejects_non_camera_urls_and_other_networks(self):
        for url in (
            'http://127.0.0.1:8080/shot.jpg',
            'http://192.168.11.22:8080/shot.jpg',
            'http://192.168.10.22:8000/shot.jpg',
            'http://192.168.10.22:8080/admin/',
            'http://192.168.10.22:8080/shot.jpg?redirect=1',
            'http://user:pass@192.168.10.22:8080/',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(self.resolve, {'url': url}).status_code, 400)

    def test_snapshot_fetch_does_not_follow_redirects(self):
        with patch('monitor.phone_camera.http.client.HTTPConnection') as connection:
            remote = connection.return_value
            remote.getresponse.return_value.status = 302
            remote.getresponse.return_value.getheader.return_value = 'text/html'
            with self.assertRaises(ValueError):
                fetch_snapshot('192.168.10.22', '/shot.jpg')
            connection.assert_called_once_with('192.168.10.22', port=8080, timeout=2)
            remote.request.assert_called_once()
            remote.close.assert_called_once()

    def test_qr_code_phone_share_and_push_flow(self):
        qr_res = self.client.post(reverse('create_phone_qr'))
        self.assertEqual(qr_res.status_code, 200)
        qr_data = qr_res.json()
        self.assertIn('<svg', qr_data['qr_svg'])
        token = qr_data['token']
        frame_url = qr_data['frame_url']

        # Before phone pushes any frame, frame_url returns 425 waiting
        waiting = self.client.get(frame_url)
        self.assertEqual(waiting.status_code, 425)

        # Phone opens share page without login
        self.client.logout()
        share_page = self.client.get(reverse('phone_camera_share', args=[token]))
        self.assertEqual(share_page.status_code, 200)
        self.assertContains(share_page, 'Share Cam')

        # Phone pushes a JPEG frame
        buf = BytesIO()
        Image.new('RGB', (320, 180), '#335544').save(buf, 'JPEG')
        push_res = self.client.post(
            reverse('phone_camera_push', args=[token]),
            {'frame': SimpleUploadedFile('frame.jpg', buf.getvalue(), 'image/jpeg')},
        )
        self.assertEqual(push_res.status_code, 200)
        self.assertEqual(push_res.json()['sequence'], 1)

        # Computer fetches frame
        self.client.force_login(self.owner)
        frame_res = self.client.get(frame_url)
        self.assertEqual(frame_res.status_code, 200)
        self.assertEqual(frame_res['Content-Type'], 'image/jpeg')
        self.assertEqual(self.client.get(frame_url, headers={'If-None-Match': frame_res['ETag']}).status_code, 304)
