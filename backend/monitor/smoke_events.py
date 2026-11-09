"""Group CCTV detections and retain the first and strongest evidence for each event."""
import io
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Alert, Analysis, SmokeCapture, SmokeEvent


def capture_summary(capture):
    return {
        'id': str(capture.pk),
        'source': capture.source,
        'source_key': str(capture.source_key) if capture.source_key else '',
        'position': capture.position,
        'score': round(capture.score, 2),
        'confidence': round(capture.confidence, 3),
        'elapsed_ms': capture.elapsed_ms,
        'created_at': timezone.localtime(capture.created_at).strftime('%H:%M:%S %d/%m/%Y'),
        'created_iso': capture.created_at.isoformat(),
        'original_url': reverse('capture_image', args=[capture.pk, 'original']),
        'annotated_url': reverse('capture_image', args=[capture.pk, 'annotated']),
        'event_id': str(capture.alert_id) if capture.alert_id else None,
        'event_url': reverse('detail', args=[capture.alert.analysis_id]) if capture.alert_id else None,
    }


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


def _create_capture(*, event, alert, owner, source, key, position, score, confidence, detections, elapsed_ms, images, written):
    capture = SmokeCapture(
        event=event, alert=alert, owner=owner, source=source, source_key=key,
        position=position, score=score, confidence=confidence,
        detections=detections, elapsed_ms=elapsed_ms,
    )
    save_pair(capture, 'original', 'annotated', images, written)
    capture.save()
    return capture


def purge_source_alerts(owner, source=None, key=None):
    """Purge all Alert, SmokeCapture, and SmokeEvent records and image files for a video/screen when replaying."""
    with transaction.atomic():
        keys = []
        if key:
            try:
                raw_u = UUID(str(key))
                derived_u = uuid5(NAMESPACE_URL, f'forestwatch:{owner.pk}:{raw_u}')
                keys.extend([raw_u, derived_u])
            except (ValueError, TypeError):
                keys.append(key)

        channel_prefix = source.split('·')[0].strip() if source and '·' in source else (source or '')
        filter_q = Q(analysis__owner=owner)
        or_cond = Q()
        if keys:
            or_cond |= Q(smoke_event__source_key__in=keys)
        if source:
            or_cond |= Q(analysis__source=source)
            if channel_prefix:
                or_cond |= Q(analysis__source__startswith=channel_prefix)
        if not keys and not source:
            return 0

        filter_q &= or_cond
        alerts_to_delete = list(
            Alert.objects.filter(filter_q)
            .select_related('analysis', 'smoke_event')
            .prefetch_related('captures')
        )
        count = len(alerts_to_delete)
        for alert in alerts_to_delete:
            for capture in alert.captures.all():
                if capture.original:
                    capture.original.delete(save=False)
                if capture.annotated:
                    capture.annotated.delete(save=False)
            if hasattr(alert, 'smoke_event') and alert.smoke_event:
                ev = alert.smoke_event
                if ev.peak_original:
                    ev.peak_original.delete(save=False)
                if ev.peak_annotated:
                    ev.peak_annotated.delete(save=False)
            analysis = alert.analysis
            if analysis.original:
                analysis.original.delete(save=False)
            if analysis.annotated:
                analysis.annotated.delete(save=False)
            analysis.delete()

        cap_q = Q(owner=owner)
        cap_or = Q()
        if keys:
            cap_or |= Q(source_key__in=keys)
        if source:
            cap_or |= Q(source=source)
            if channel_prefix:
                cap_or |= Q(source__startswith=channel_prefix)
        cap_q &= cap_or

        for cap in SmokeCapture.objects.filter(cap_q):
            if cap.original:
                cap.original.delete(save=False)
            if cap.annotated:
                cap.annotated.delete(save=False)
        SmokeCapture.objects.filter(cap_q).delete()

        if keys:
            SmokeEvent.objects.filter(source_key__in=keys).delete()
        if source:
            ev_q = Q(alert__analysis__owner=owner) & (Q(alert__analysis__source=source) | Q(alert__analysis__source__startswith=channel_prefix))
            SmokeEvent.objects.filter(ev_q).delete()

        return count


def track_smoke(*, owner, source, key, kind, position, moving, mode, model_name, detections, images, elapsed_ms, video_replayed=False):
    if not detections and not moving:
        return None, False
    # A concurrent first frame may insert the open event before this request does.
    for attempt in range(2):
        try:
            return _track_smoke(owner, source, key, kind, position, moving, mode, model_name, detections, images, elapsed_ms, video_replayed=video_replayed)
        except IntegrityError:
            if attempt or not SmokeEvent.objects.filter(source_key=key, closed=False).exists():
                raise


def _track_smoke(owner, source, key, kind, position, moving, mode, model_name, detections, images, elapsed_ms, video_replayed=False):
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

            # If video replayed or rewound from a later point back to beginning, automatically purge old alerts of this video
            replayed = video_replayed
            if not replayed and kind in ('video', 'url') and position is not None:
                if position <= 1.5:
                    if event and ((event.peak_position is not None and event.peak_position > 2.5) or event.positive_samples > 1):
                        replayed = True
                elif event and (
                    (event.first_position is not None and position < event.first_position - 1.0) or
                    (event.peak_position is not None and position < event.peak_position - 1.5)
                ):
                    replayed = True

            if replayed:
                purge_source_alerts(owner, source=source, key=key)
                event = None

            if not detections:
                if event and moving:
                    event.clear_samples += 1
                    event.closed = event.clear_samples >= 3
                    event.save(update_fields=['clear_samples', 'closed'])
                return None, False

            # Coverage compares FoSp frames within an event. YOLO provides confidence instead.
            score = max(float(d.get('mask_coverage', d.get('confidence')) or 0) for d in detections)
            confidence = max(float(d.get('confidence') or 0) for d in detections)
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
                capture = _create_capture(
                    event=event, alert=alert, owner=owner, source=source, key=key,
                    position=position, score=score, confidence=confidence,
                    detections=detections, elapsed_ms=elapsed_ms, images=images, written=written,
                )
                summary = event_summary(alert, event)
                summary['latest_capture'] = capture_summary(capture)
                return summary, True

            event.last_seen = now
            event.clear_samples = 0
            latest_capture = None
            if moving:
                # Prevent duplicate captures for the same video timestamp within 0.45s
                is_duplicate = False
                if position is not None:
                    is_duplicate = event.captures.filter(
                        position__isnull=False,
                        position__gte=position - 0.45,
                        position__lte=position + 0.45,
                    ).exists()

                if not is_duplicate:
                    event.positive_samples += 1
                    latest_capture = _create_capture(
                        event=event, alert=event.alert, owner=owner, source=source, key=key,
                        position=position, score=score, confidence=confidence,
                        detections=detections, elapsed_ms=elapsed_ms, images=images, written=written,
                    )
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
            summary = event_summary(event.alert, event)
            if latest_capture:
                summary['latest_capture'] = capture_summary(latest_capture)
            return summary, False
    except Exception:
        for storage, name in written:
            storage.delete(name)
        raise
