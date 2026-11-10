import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .models import Alert, SmokeCapture, SmokeEvent


class SmokeEventsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user('event-owner')
        cls.other = get_user_model().objects.create_user('event-other')

    def setUp(self):
        self.client.force_login(self.owner)
        self.key = uuid4().hex
        self.media = self.enterContext(TemporaryDirectory())
        self.enterContext(self.settings(MEDIA_ROOT=self.media, INFERENCE_MODE='fosp'))

    def scan(self, score=None, *, confidence=0.8, position=0, kind='video', moving=True, tracked=True):
        image = Image.new('RGB', (96, 64), '#516143')
        payload = io.BytesIO()
        image.save(payload, 'JPEG')
        detections = [] if score is None else [{'label': 'smoke', 'mask_coverage': score, 'confidence': confidence}]
        with patch('monitor.views.predict', return_value=(detections, image)):
            response = self.client.post(reverse('scan_frame'), {
                'source_key': self.key if tracked else '', 'source_kind': kind,
                'source': 'CAM 01 · Trạm rừng', 'save_alert': '1',
                'position': position, 'moving': int(moving),
                'image': SimpleUploadedFile('frame.jpg', payload.getvalue(), 'image/jpeg'),
            })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_first_and_strongest_frames_stay_in_one_event_across_page_loads(self):
        first = self.scan(2.0, position=0)
        self.assertTrue(first['event_created'])
        room = self.client.get(reverse('control_room'))
        self.assertContains(room, first['event']['id'])
        second = self.scan(5.5, position=8)
        self.assertFalse(second['event_created'])
        self.assertEqual(first['event']['id'], second['event']['id'])
        self.scan(4.1, position=10)
        event = SmokeEvent.objects.get()
        self.assertEqual(event.first_position, 0)
        self.assertEqual(event.peak_position, 8)
        self.assertEqual(event.positive_samples, 3)
        self.assertEqual(event.alert.analysis.detections[0]['mask_coverage'], 2.0)
        self.assertContains(self.client.get(first['event']['url']), 'Khung có vùng khói lớn nhất')
        download_url = reverse('evidence', args=[event.alert.analysis_id, 'peak_annotated']) + '?download=1'
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download['Content-Disposition'].startswith('attachment;'))
        download.close()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_evidence_requires_40_percent_confidence_independent_of_mask_coverage(self):
        below = self.scan(50.0, confidence=0.39999)
        self.assertTrue(below['has_smoke'])
        self.assertFalse(below['alert'])
        self.assertFalse(Alert.objects.exists())
        self.assertFalse(self.scan(50.0, confidence=None)['alert'])
        first = self.scan(0.5, confidence=0.4)
        self.assertTrue(first['event_created'])
        self.scan(60.0, confidence=0.39)
        event = SmokeEvent.objects.get()
        self.assertFalse(event.peak_annotated)
        self.assertEqual(event.positive_samples, 1)
        self.assertContains(self.client.get(reverse('alerts')), reverse('delete_alerts'))

    def test_legacy_scan_save_flag_also_respects_evidence_threshold(self):
        self.assertIsNone(self.scan(50, confidence=0.39, tracked=False)['evidence_url'])
        self.assertIsNotNone(self.scan(1, confidence=0.4, tracked=False)['evidence_url'])
        self.assertEqual(Alert.objects.count(), 1)

    def test_paused_clear_samples_do_not_close_event_and_three_moving_samples_do(self):
        first = self.scan(1.0)
        for _ in range(3):
            self.scan(moving=False)
        self.assertFalse(SmokeEvent.objects.get().closed)
        for index in range(3):
            self.scan(position=index + 1)
        self.assertTrue(SmokeEvent.objects.get().closed)
        next_event = self.scan(1.0, position=4)
        self.assertTrue(next_event['event_created'])
        self.assertNotEqual(first['event']['id'], next_event['event']['id'])

    def test_same_saved_still_image_does_not_add_alerts_on_rescan(self):
        self.assertTrue(self.scan(3.2, kind='image', moving=False)['event_created'])
        self.assertFalse(self.scan(3.2, kind='image', moving=False)['event_created'])
        self.assertEqual(Alert.objects.count(), 1)
        self.assertEqual(SmokeEvent.objects.get().positive_samples, 1)

    def test_source_keys_are_scoped_to_each_account(self):
        first = self.scan(3.2)
        self.client.force_login(self.other)
        second = self.scan(3.2)
        self.assertTrue(second['event_created'])
        self.assertNotEqual(first['event']['id'], second['event']['id'])

    def test_changing_model_keeps_evidence_in_separate_events(self):
        with self.settings(FOSP_MODEL_PATH=Path('best.pth')):
            first = self.scan(3.2, kind='phone')
        with self.settings(FOSP_MODEL_PATH=Path('E3_concat_best.pth')):
            second = self.scan(4.2, kind='phone')
        self.assertTrue(second['event_created'])
        self.assertNotEqual(first['event']['id'], second['event']['id'])
        first_event = SmokeEvent.objects.get(alert_id=first['event']['id'])
        second_event = SmokeEvent.objects.get(alert_id=second['event']['id'])
        self.assertTrue(first_event.closed)
        self.assertEqual(first_event.alert.analysis.model_name, 'best.pth')
        self.assertEqual(second_event.alert.analysis.model_name, 'E3_concat_best.pth')

    def test_per_screen_captures_and_screen_separation(self):
        self.scan(2.5, confidence=0.72, position=1.0)
        self.scan(3.8, confidence=0.81, position=2.5)
        self.scan(4.2, confidence=0.88, position=4.0)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 3)

        alerts_res = self.client.get(reverse('alerts'), {'source': 'CAM 01 · Trạm rừng'})
        self.assertEqual(alerts_res.status_code, 200)
        self.assertContains(alerts_res, '3 khung hình đã chụp')

        caps = list(SmokeCapture.objects.filter(owner=self.owner))
        del_res = self.client.post(reverse('delete_alerts'), {'capture_ids': [str(caps[0].pk)]})
        self.assertEqual(del_res.status_code, 302)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 2)

    def test_video_replay_automatically_deletes_old_alerts_and_captures(self):
        first = self.scan(2.5, position=2.0)
        self.assertTrue(first['event_created'])
        second = self.scan(4.0, position=8.0)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 2)

        # Video replays back to beginning (position 0.5 < first_position 2.0)
        replayed = self.scan(3.1, position=0.5)
        self.assertTrue(replayed['event_created'])
        # Old captures should have been cleared, leaving only the new one from position 0.5
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 1)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).first().position, 0.5)

    def test_duplicate_captures_at_near_identical_timestamps_are_skipped(self):
        self.scan(2.0, position=3.0)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 1)
        # Scan again at position 3.2 (within 0.45s)
        self.scan(2.5, position=3.2)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 1)
        # Scan at position 4.5 (different timestamp)
        self.scan(3.0, position=4.5)
        self.assertEqual(SmokeCapture.objects.filter(owner=self.owner).count(), 2)
