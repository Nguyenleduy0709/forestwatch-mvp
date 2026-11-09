import io
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .video_sources import playable_youtube_stream


class VideoSourcesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('video-owner')
        cls.other = get_user_model().objects.create_user('another-operator')

    def setUp(self):
        self.client.force_login(self.owner)
        self.resolve = reverse('resolve_video')

    def test_private_network_source_is_rejected(self):
        for url in ('http://127.0.0.1/video.mp4', 'http://[::1]/video.mp4', 'file:///video.mp4'):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(self.resolve, {'url': url}).status_code, 400)

    def test_public_video_ranges_are_relayed_to_owner(self):
        with patch('monitor.video_sources.public_addresses'):
            resolved = self.client.post(self.resolve, {'url': 'https://media.example/video.mp4'})
        self.assertEqual(resolved.status_code, 200)
        url = resolved.json()['url']
        remote = Mock(status=206)
        remote.getheader.side_effect = {
            'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-3/20',
            'Content-Length': '4', 'Accept-Ranges': 'bytes',
        }.get
        remote.read = io.BytesIO(b'ftyp').read
        connection = Mock()
        with patch('monitor.video_sources.open_video', return_value=(remote, connection)) as fetch:
            response = self.client.get(url, headers={'Range': 'bytes=0-3'})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response['Content-Range'], 'bytes 0-3/20')
            self.assertEqual(b''.join(response.streaming_content), b'ftyp')
            fetch.assert_called_once_with(
                'https://media.example/video.mp4', {'Accept-Encoding': 'identity', 'Range': 'bytes=0-3'},
            )
            connection.close.assert_called()
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_redirect_to_private_host_is_rejected(self):
        with patch('monitor.video_sources.public_addresses'):
            resolved = self.client.post(self.resolve, {'url': 'https://8.8.8.8/video.mp4'})
        remote = Mock(status=302)
        remote.getheader.return_value = 'http://127.0.0.1/video.mp4'
        with patch('monitor.video_sources.http.client.HTTPSConnection') as https:
            https.return_value.getresponse.return_value = remote
            response = self.client.get(resolved.json()['url'])
        self.assertEqual(response.status_code, 502)
        remote.close.assert_called_once()

    def test_youtube_link_resolves_a_video_stream(self):
        with patch('monitor.video_sources.resolve_youtube', return_value=(
            'https://edge.googlevideo.com/videoplayback', {}, 'mp4',
        )) as resolve:
            response = self.client.post(self.resolve, {'url': 'https://youtu.be/dQw4w9WgXcQ?t=25'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['type'], 'YouTube')
        self.assertTrue(response.json()['url'].startswith('/api/video/media/'))
        resolve.assert_called_once_with('dQw4w9WgXcQ')

    def test_youtube_uses_another_format_if_the_preferred_stream_is_blocked(self):
        blocked = 'https://edge.googlevideo.com/blocked'
        playable = 'https://edge.googlevideo.com/playable'
        preferred = {'url': blocked, 'ext': 'mp4', 'protocol': 'https', 'vcodec': 'avc1'}
        second = {**preferred, 'url': playable, 'ext': 'webm', 'vcodec': 'vp9'}
        responses = [(Mock(status=403), Mock()), (Mock(status=206), Mock())]
        with patch('monitor.video_sources.open_video', side_effect=responses):
            self.assertEqual(playable_youtube_stream({
                **preferred, 'formats': [second, preferred],
            }), (playable, {}, 'webm'))
        for response, connection in responses:
            response.close.assert_called_once()
            connection.close.assert_called_once()
