"""Group CCTV detections and retain the first and strongest evidence for each event."""
import io
from datetime import timedelta
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Alert, Analysis, SmokeEvent


def event_summary(alert, event):
    return {
        'id': str(alert.pk),
        'source': alert.analysis.source,
        'at': alert.created_at.isoformat(),
        'position': event.first_position,
        'samples': event.positive_samples,
        'url': reverse('detail', args=[alert.analysis_id]),
        'thumbnail': reverse('evidence', args=[alert.analysis_id, 'annotated']),
    }


def save_pair(record, first_field, second_field, images, written):
    filename = f'{uuid4().hex}.jpg'
    for field_name, image in zip((first_field, second_field), images):
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
        field = getattr(record, field_name)
        field.save(filename, ContentFile(buffer.getvalue()), save=False)
        written.append((field.storage, field.name))


def track_smoke(*, owner, source, key, kind, position, moving, mode, model_name, detections, images, elapsed_ms):
    if not detections and not moving:
        return None, False
    # A concurrent first frame may insert the open event before this request does.
    for attempt in range(2):
        try:
            return _track_smoke(owner, source, key, kind, position, moving, mode, model_name, detections, images, elapsed_ms)
        except IntegrityError:
            if attempt or not SmokeEvent.objects.filter(source_key=key, closed=False).exists():
                raise


def _track_smoke(owner, source, key, kind, position, moving, mode, model_name, detections, images, elapsed_ms):
    now = timezone.now()
    written = []
    replaced = []
    try:
        with transaction.atomic():
            current = SmokeEvent.objects.filter(source_key=key, closed=False)
            # The write also serializes episode changes on SQLite before reading the open row.
            stale = Q(last_seen__lt=now - timedelta(minutes=5)) & ~Q(source_kind='image')
            changed_model = ~Q(alert__analysis__model_name=model_name)
            current.filter(stale | changed_model).update(closed=True)
            event = current.select_for_update().first()
            if not detections:
                if event and moving:
                    event.clear_samples += 1
                    event.closed = event.clear_samples >= 3
                    event.save(update_fields=['clear_samples', 'closed'])
                return None, False

            # Coverage compares FoSp frames within an event. YOLO provides confidence instead.
            score = max(float(d.get('mask_coverage', d.get('confidence')) or 0) for d in detections)
            if event is None:
                run = Analysis(owner=owner, source=source, mode=mode, model_name=model_name,
                               detections=detections, elapsed_ms=elapsed_ms)
                save_pair(run, 'original', 'annotated', images, written)
                run.save()
                alert = Alert.objects.create(analysis=run)
                event = SmokeEvent.objects.create(
                    alert=alert, source_key=key, source_kind=kind, first_position=position,
                    last_seen=now, peak_score=score,
                )
                return event_summary(alert, event), True

            event.last_seen = now
            event.clear_samples = 0
            if moving:
                event.positive_samples += 1
            changed = ['last_seen', 'clear_samples', 'positive_samples']
            if score > event.peak_score:
                replaced = [(field.storage, field.name) for field in (event.peak_original, event.peak_annotated) if field]
                save_pair(event, 'peak_original', 'peak_annotated', images, written)
                event.peak_score = score
                event.peak_at = now
                event.peak_position = position
                changed += ['peak_original', 'peak_annotated', 'peak_score', 'peak_at', 'peak_position']
            event.save(update_fields=changed)
            for storage, name in replaced:
                transaction.on_commit(lambda storage=storage, name=name: storage.delete(name), robust=True)
            return event_summary(event.alert, event), False
    except Exception:
        for storage, name in written:
            storage.delete(name)
        raise
