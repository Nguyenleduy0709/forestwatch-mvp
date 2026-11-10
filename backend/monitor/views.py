import base64
import io
import json
import logging
from math import isfinite
from time import perf_counter
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from PIL import Image
from .forms import UploadForm, process_pil_image, fetch_image_from_url
from .inference import EVIDENCE_CONFIDENCE, SMOKE_LABELS, current_model_name, predict, qualifies_for_evidence
from .models import Analysis, Alert, AlertHistory, SmokeCapture, SmokeEvent
from .smoke_events import capture_summary, event_summary, purge_source_alerts, track_smoke
from .video_sources import relay_source, resolve_source
from .auth_views import (
    register,
    google_login,
    google_callback,
    google_set_password,
    logout_view,
)

def home(request):
    return render(request, 'home.html')

def context():
    model_name = current_model_name()
    return {'mode': settings.INFERENCE_MODE, 'model_name': model_name,
            'reference_enabled': settings.INFERENCE_MODE == 'fosp' and model_name.lower().startswith('e3_concat')}

@login_required
def dashboard(request):
    runs = Analysis.objects.filter(owner=request.user)
    alerts = Alert.objects.filter(analysis__owner=request.user)
    return render(request, 'dashboard.html', {**context(), 'form': UploadForm(), 'runs': runs[:6],
        'total': runs.count(), 'pending': alerts.filter(status='PENDING').count(),
        'confirmed': alerts.filter(status='CONFIRMED').count(), 'alert_count': alerts.count()})

@login_required
@require_POST
def analyze(request):
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, ' '.join(str(e) for errors in form.errors.values() for e in errors))
        return redirect('dashboard')
    started = perf_counter()
    simulate = form.cleaned_data.get('simulate_smoke', False)
    try:
        detections, annotated = predict(
            form.normalized_image,
            simulate,
        )
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect('dashboard')
    except Exception:
        logging.getLogger(__name__).exception('Inference failed')
        messages.error(request, 'Phân tích thất bại. Kiểm tra trọng số, bộ nhớ và log trên máy chạy ứng dụng.')
        return redirect('dashboard')
    run = Analysis(owner=request.user, source=form.cleaned_data['source'], mode=settings.INFERENCE_MODE,
                   model_name=current_model_name(),
                   detections=detections, elapsed_ms=round((perf_counter()-started)*1000))
    for field, image in [('original', form.normalized_image), ('annotated', annotated)]:
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
        getattr(run, field).save(f'{run.id}.jpg', ContentFile(buffer.getvalue()), save=False)
    with transaction.atomic():
        run.save()
        if any(qualifies_for_evidence(d, simulated=simulate and settings.INFERENCE_MODE == 'demo') for d in detections):
            Alert.objects.create(analysis=run)
    return redirect('detail', pk=run.pk)

@login_required
def detail(request, pk):
    _ensure_captures_for_user(request.user)
    run = get_object_or_404(Analysis.objects.select_related('alert__smoke_event'), pk=pk, owner=request.user)
    alert = getattr(run, 'alert', None)
    event = getattr(alert, 'smoke_event', None)
    captures = list(SmokeCapture.objects.filter(owner=request.user, alert=alert).order_by('-created_at')[:60]) if alert else []
    screen_captures = list(
        SmokeCapture.objects.filter(
            Q(source_key=event.source_key) if event else Q(source=run.source),
            owner=request.user,
        ).order_by('-created_at')[:60]
    )
    return render(request, 'detail.html', {
        **context(), 'run': run, 'form': UploadForm(), 'event': event,
        'captures': captures, 'screen_captures': screen_captures,
    })

@login_required
def evidence(request, pk, kind):
    run = get_object_or_404(Analysis, pk=pk, owner=request.user)
    if kind in {'original', 'annotated'}:
        file = getattr(run, kind)
    elif kind in {'peak_original', 'peak_annotated'}:
        event = get_object_or_404(SmokeEvent, alert__analysis=run)
        file = getattr(event, kind)
    else:
        raise Http404
    if not file:
        raise Http404
    try:
        response = FileResponse(
            file.open('rb'), content_type='image/jpeg', as_attachment=request.GET.get('download') == '1',
            filename=f'forestwatch-{run.pk}-{kind}.jpg',
        )
    except FileNotFoundError as exc:
        raise Http404 from exc
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def capture_image(request, pk, kind):
    if kind not in {'original', 'annotated'}:
        raise Http404
    capture = get_object_or_404(SmokeCapture, pk=pk, owner=request.user)
    file = getattr(capture, kind)
    if not file:
        raise Http404
    try:
        response = FileResponse(
            file.open('rb'), content_type='image/jpeg', as_attachment=request.GET.get('download') == '1',
            filename=f'forestwatch-capture-{capture.pk}-{kind}.jpg',
        )
    except FileNotFoundError as exc:
        raise Http404 from exc
    response['Cache-Control'] = 'private, no-store'
    return response


def _ensure_captures_for_user(user):
    """Backfill SmokeCapture records for any pre-existing Alerts so every screen's history is complete."""
    missing = list(
        Alert.objects.filter(analysis__owner=user, captures__isnull=True)
        .select_related('analysis', 'smoke_event')[:100]
    )
    if not missing:
        return
    for alert in missing:
        run = alert.analysis
        ev = getattr(alert, 'smoke_event', None)
        dets = run.detections or []
        score = max((float(d.get('mask_coverage', d.get('confidence')) or 0) for d in dets), default=0.0)
        conf = max((float(d.get('confidence') or 0) for d in dets), default=0.0)
        cap = SmokeCapture.objects.create(
            event=ev,
            alert=alert,
            owner=user,
            source=run.source,
            source_key=ev.source_key if ev else None,
            position=ev.first_position if ev else None,
            score=score,
            confidence=conf,
            detections=dets,
            elapsed_ms=run.elapsed_ms,
            original=run.original.name if run.original else '',
            annotated=run.annotated.name if run.annotated else '',
        )
        SmokeCapture.objects.filter(pk=cap.pk).update(created_at=alert.created_at)
        # Only create a peak capture if it represents a genuinely distinct frame and timestamp
        if (ev and ev.peak_annotated and ev.peak_original and
            ev.peak_original.name and run.original and ev.peak_original.name != run.original.name and
            ev.peak_position is not None and ev.first_position is not None and
            abs(ev.peak_position - ev.first_position) > 0.6):
            peak_cap = SmokeCapture.objects.create(
                event=ev,
                alert=alert,
                owner=user,
                source=run.source,
                source_key=ev.source_key,
                position=ev.peak_position,
                score=ev.peak_score or score,
                confidence=conf,
                detections=dets,
                elapsed_ms=run.elapsed_ms,
                original=ev.peak_original.name,
                annotated=ev.peak_annotated.name,
            )
            if ev.peak_at:
                SmokeCapture.objects.filter(pk=peak_cap.pk).update(created_at=ev.peak_at)


@login_required
def alerts(request):
    _ensure_captures_for_user(request.user)
    all_items = Alert.objects.filter(analysis__owner=request.user)
    all_captures = SmokeCapture.objects.filter(owner=request.user)
    status = request.GET.get('status', '')
    selected_source = request.GET.get('source', '').strip()

    alert_sources = list(all_items.order_by().values_list('analysis__source', flat=True).distinct())
    capture_sources = list(all_captures.order_by().values_list('source', flat=True).distinct())
    sources = list(dict.fromkeys(capture_sources + alert_sources))

    if not selected_source and sources:
        selected_source = sources[0]

    screen_tabs = []
    for src in sources:
        src_caps = all_captures.filter(source=src)
        if status in Alert.Status.values:
            src_caps = src_caps.filter(alert__status=status)
        cap_count = src_caps.count()
        latest_obj = src_caps.order_by('-created_at').first()
        tab_url = f"{reverse('alerts')}?source={quote(src)}" + (f"&status={status}" if status in Alert.Status.values else "")
        screen_tabs.append({
            'source': src,
            'capture_count': cap_count,
            'alert_count': all_items.filter(analysis__source=src).count(),
            'latest_at': latest_obj.created_at if latest_obj else None,
            'url': tab_url,
            'active': src == selected_source,
        })

    items = all_items.select_related('analysis', 'smoke_event')
    captures_qs = all_captures.select_related('alert', 'alert__analysis', 'event')
    if status in Alert.Status.values:
        items = items.filter(status=status)
        captures_qs = captures_qs.filter(alert__status=status)
    if selected_source:
        items = items.filter(analysis__source=selected_source)
        captures_qs = captures_qs.filter(source=selected_source)

    screen_captures = list(captures_qs.order_by('-created_at')[:150])
    try:
        delete_alerts_url = reverse('delete_alerts')
    except NoReverseMatch:
        delete_alerts_url = None

    return render(request, 'alerts.html', {
        **context(), 'alerts': items[:100], 'status': status,
        'selected_source': selected_source, 'sources': sources,
        'screen_tabs': screen_tabs,
        'screen_captures': screen_captures,
        'delete_alerts_url': delete_alerts_url,
        'pending_count': all_items.filter(status=Alert.Status.PENDING).count(),
        'statuses': Alert.Status.choices,
    })

@login_required
@require_POST
def transition(request, pk):
    alert = get_object_or_404(Alert, pk=pk, analysis__owner=request.user)
    target = request.POST.get('status', '')
    allowed = {'PENDING': {'CONFIRMED', 'REJECTED'}, 'CONFIRMED': {'RESOLVED'}}
    if target not in allowed.get(alert.status, set()):
        messages.error(request, 'Thao tác không hợp lệ với trạng thái hiện tại.')
        return redirect('detail', pk=alert.analysis_id)
    with transaction.atomic():
        changed = Alert.objects.filter(pk=pk, status=alert.status).update(status=target, updated_at=timezone.now())
        if changed:
            AlertHistory.objects.create(alert=alert, actor=request.user, old_status=alert.status,
                                        new_status=target, note=request.POST.get('note', '')[:500])
        else:
            messages.error(request, 'Trạng thái vừa thay đổi. Hãy tải lại trước khi thao tác.')
    return redirect('detail', pk=alert.analysis_id)


@login_required
@require_POST
def delete_alerts(request):
    """Delete selected captures/alerts or all captures/alerts matching screen/status."""
    alert_ids = request.POST.getlist('alert_ids')
    single_id = request.POST.get('alert_id')
    capture_ids = request.POST.getlist('capture_ids')
    single_capture_id = request.POST.get('capture_id')
    delete_all = request.POST.get('delete_all') in ('true', '1', 'on')
    status_filter = request.POST.get('status', '').strip()
    source_filter = request.POST.get('source', '').strip()
    source_key_param = request.POST.get('source_key', '').strip()

    if single_id and single_id not in alert_ids:
        alert_ids.append(single_id)
    if single_capture_id and single_capture_id not in capture_ids:
        capture_ids.append(single_capture_id)

    if not alert_ids and not capture_ids and not delete_all and request.body:
        try:
            payload = json.loads(request.body)
            if isinstance(payload.get('alert_ids'), list):
                alert_ids = payload['alert_ids']
            elif payload.get('alert_id'):
                alert_ids = [payload['alert_id']]
            if isinstance(payload.get('capture_ids'), list):
                capture_ids = payload['capture_ids']
            elif payload.get('capture_id'):
                capture_ids = [payload['capture_id']]
            if payload.get('delete_all'):
                delete_all = True
            if payload.get('status'):
                status_filter = payload['status']
            if payload.get('source'):
                source_filter = payload['source']
            if payload.get('source_key'):
                source_key_param = payload['source_key']
        except (ValueError, KeyError):
            pass

    derived_source_key = None
    if source_key_param:
        try:
            derived_source_key = uuid5(NAMESPACE_URL, f'forestwatch:{request.user.pk}:{UUID(str(source_key_param))}')
        except (ValueError, TypeError):
            try:
                derived_source_key = UUID(str(source_key_param))
            except (ValueError, TypeError):
                derived_source_key = None

    if delete_all and (source_filter or derived_source_key):
        deleted_count = purge_source_alerts(request.user, source=source_filter, key=derived_source_key)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': True, 'deleted_count': deleted_count, 'message': f'Đã xóa toàn bộ cảnh báo của nguồn này thành công.'})
        messages.success(request, f'Đã xóa toàn bộ cảnh báo của nguồn này thành công.')
        if source_filter:
            return redirect(f"{reverse('alerts')}?source={quote(source_filter)}")
        return redirect('alerts')

    if capture_ids and not alert_ids and not delete_all:
        caps_qs = SmokeCapture.objects.filter(owner=request.user, pk__in=capture_ids).select_related('alert__analysis', 'event')
        caps_to_delete = list(caps_qs)
        count = len(caps_to_delete)
        affected_alert_ids = {cap.alert_id for cap in caps_to_delete if cap.alert_id}
        with transaction.atomic():
            for cap in caps_to_delete:
                run = cap.alert.analysis if cap.alert_id else None
                shared_orig = run and run.original and cap.original and (run.original.name == cap.original.name)
                shared_anno = run and run.annotated and cap.annotated and (run.annotated.name == cap.annotated.name)
                if cap.original and not shared_orig:
                    cap.original.delete(save=False)
                if cap.annotated and not shared_anno:
                    cap.annotated.delete(save=False)
                cap.delete()
            for a_id in affected_alert_ids:
                if not SmokeCapture.objects.filter(alert_id=a_id).exists():
                    alert_obj = Alert.objects.filter(pk=a_id, analysis__owner=request.user).select_related('analysis', 'smoke_event').first()
                    if alert_obj:
                        if hasattr(alert_obj, 'smoke_event') and alert_obj.smoke_event:
                            ev = alert_obj.smoke_event
                            if ev.peak_original:
                                ev.peak_original.delete(save=False)
                            if ev.peak_annotated:
                                ev.peak_annotated.delete(save=False)
                        analysis = alert_obj.analysis
                        if analysis.original:
                            analysis.original.delete(save=False)
                        if analysis.annotated:
                            analysis.annotated.delete(save=False)
                        analysis.delete()

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': True, 'deleted_count': count, 'message': f'Đã xóa {count} bản ghi quét thành công.'})
        messages.success(request, f'Đã xóa {count} bản ghi quét thành công.')
        if source_filter:
            return redirect(f"{reverse('alerts')}?source={quote(source_filter)}")
        referrer = request.META.get('HTTP_REFERER')
        if referrer and '/alerts/' in referrer:
            return redirect(referrer)
        return redirect('alerts')

    qs = Alert.objects.filter(analysis__owner=request.user)

    if delete_all:
        if status_filter in Alert.Status.values:
            qs = qs.filter(status=status_filter)
        if source_filter:
            qs = qs.filter(analysis__source=source_filter)
    elif alert_ids:
        qs = qs.filter(pk__in=alert_ids)
    else:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': False, 'message': 'Chưa chọn cảnh báo nào để xóa.'}, status=400)
        messages.warning(request, 'Chưa chọn cảnh báo nào để xóa.')
        return redirect('alerts')

    alerts_to_delete = list(qs.select_related('analysis', 'smoke_event').prefetch_related('captures'))
    count = len(alerts_to_delete)

    with transaction.atomic():
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
        if delete_all and source_filter:
            orphan_caps = SmokeCapture.objects.filter(owner=request.user, source=source_filter)
            for cap in orphan_caps:
                if cap.original:
                    cap.original.delete(save=False)
                if cap.annotated:
                    cap.annotated.delete(save=False)
            orphan_caps.delete()

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'success': True, 'deleted_count': count, 'message': f'Đã xóa {count} cảnh báo thành công.'})

    messages.success(request, f'Đã xóa {count} cảnh báo thành công.')
    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    referrer = request.META.get('HTTP_REFERER')
    if referrer and '/alerts/' in referrer:
        return redirect(referrer)
    return redirect('alerts')


@login_required
@require_GET
def screen_reports_api(request):
    """Return continuous smoke captures and alert events for a specific monitoring screen."""
    _ensure_captures_for_user(request.user)
    raw_key = request.GET.get('source_key', '').strip()
    source_name = request.GET.get('source', '').strip()
    alert_id = request.GET.get('alert_id', '').strip()

    captures_qs = SmokeCapture.objects.filter(owner=request.user).select_related('alert', 'alert__analysis')
    alerts_qs = Alert.objects.filter(analysis__owner=request.user).select_related('analysis', 'smoke_event')

    key_filter = Q()
    if raw_key:
        try:
            parsed_uuid = UUID(raw_key)
            derived_key = uuid5(NAMESPACE_URL, f'forestwatch:{request.user.pk}:{parsed_uuid}')
            key_filter |= Q(source_key__in=[parsed_uuid, derived_key])
        except ValueError:
            pass

    if key_filter and source_name:
        captures_qs = captures_qs.filter(key_filter | Q(source=source_name))
        alerts_qs = alerts_qs.filter(Q(smoke_event__source_key__in=[parsed_uuid, derived_key]) | Q(analysis__source=source_name))
    elif key_filter:
        captures_qs = captures_qs.filter(key_filter)
        alerts_qs = alerts_qs.filter(smoke_event__source_key__in=[parsed_uuid, derived_key])
    elif source_name:
        captures_qs = captures_qs.filter(source=source_name)
        alerts_qs = alerts_qs.filter(analysis__source=source_name)
    elif alert_id:
        captures_qs = captures_qs.filter(alert_id=alert_id)
        alerts_qs = alerts_qs.filter(pk=alert_id)

    total_captures = captures_qs.count()
    total_alerts = alerts_qs.count()
    captures_list = list(captures_qs[:100])
    alerts_list = list(alerts_qs[:30])

    seen_caps = set()
    unique_captures = []
    for c in captures_list:
        pos_key = round(c.position, 1) if c.position is not None else None
        c_key = (pos_key, c.original.name if c.original else '', str(c.alert_id) if c.alert_id else '')
        if c_key not in seen_caps:
            seen_caps.add(c_key)
            unique_captures.append(capture_summary(c))

    resolved_source = source_name or (captures_list[0].source if captures_list else (alerts_list[0].analysis.source if alerts_list else ''))
    alerts_url = f"{reverse('alerts')}?source={quote(resolved_source)}" if resolved_source else reverse('alerts')

    return JsonResponse({
        'source': resolved_source,
        'total_captures': len(unique_captures),
        'total_alerts': total_alerts,
        'alerts_url': alerts_url,
        'captures': unique_captures,
        'events': [event_summary(a, a.smoke_event) for a in alerts_list if hasattr(a, 'smoke_event') and a.smoke_event],
    })


@login_required
def control_room(request):
    alerts = Alert.objects.filter(analysis__owner=request.user)
    recent = alerts.filter(smoke_event__isnull=False).select_related('analysis', 'smoke_event')[:12]
    return render(request, 'control_room.html', {
        **context(), 'recent_events': [event_summary(alert, alert.smoke_event) for alert in recent],
        'evidence_threshold': round(EVIDENCE_CONFIDENCE * 100),
        'pending_count': alerts.filter(status=Alert.Status.PENDING).count(),
    })


@login_required
@require_POST
def resolve_video(request):
    return resolve_source(request)


@login_required
@require_GET
def relay_video(request, token):
    return relay_source(request, token)


@login_required
@require_POST
def scan_frame(request):
    """API endpoint to scan a single CCTV / Video / Camera frame for forest smoke."""
    source = request.POST.get('source', 'CAM 01 - Trạm Cột 1').strip()[:120]
    simulate_smoke = request.POST.get('simulate_smoke') in ('true', '1', 'on')
    save_alert = request.POST.get('save_alert') in ('true', '1', 'on')
    video_replayed = request.POST.get('video_replayed') in ('true', '1', 'on')
    source_key = request.POST.get('source_key')
    kind = request.POST.get('source_kind')
    position = None
    if source_key:
        try:
            source_key = uuid5(NAMESPACE_URL, f'forestwatch:{request.user.pk}:{UUID(source_key)}')
            if kind not in {'image', 'video', 'url', 'webcam', 'phone'}:
                raise ValueError
            if kind in {'video', 'url'} and request.POST.get('position'):
                position = float(request.POST['position'])
                if not isfinite(position) or position < 0:
                    raise ValueError
        except ValueError:
            return JsonResponse({'message': 'Thông tin nguồn hoặc vị trí video không hợp lệ.'}, status=400)

    image = None
    if 'image' in request.FILES:
        try:
            image = process_pil_image(request.FILES['image'])
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Lỗi ảnh: {e}'}, status=400)
    elif 'image_b64' in request.POST:
        try:
            b64_str = request.POST['image_b64']
            if ',' in b64_str:
                b64_str = b64_str.split(',', 1)[1]
            raw_bytes = base64.b64decode(b64_str)
            image = process_pil_image(io.BytesIO(raw_bytes))
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Lỗi giải mã base64: {e}'}, status=400)
    elif 'image_url' in request.POST and request.POST['image_url'].strip():
        try:
            raw_bytes = fetch_image_from_url(request.POST['image_url'].strip())
            image = process_pil_image(io.BytesIO(raw_bytes))
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Lỗi tải ảnh từ liên kết URL: {e}'}, status=400)

    if not image:
        return JsonResponse({'status': 'error', 'message': 'Không có dữ liệu khung hình ảnh.'}, status=400)

    reference = None
    if upload := request.FILES.get('reference'):
        try:
            if upload.size > 2 * 1024 * 1024:
                raise ValueError
            reference = process_pil_image(upload)
        except (ValueError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            return JsonResponse({'message': 'Ảnh nền cần là ảnh hợp lệ và không quá 2 MB.'}, status=400)
    started = perf_counter()
    try:
        detections, annotated = predict(image, simulate_smoke, reference=reference) if reference else predict(image, simulate_smoke)
    except RuntimeError as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=503)
    except Exception:
        logging.getLogger(__name__).exception('CCTV inference failed')
        return JsonResponse({'status': 'error', 'message': 'Lỗi phân tích khung hình.'}, status=500)

    elapsed_ms = round((perf_counter() - started) * 1000)
    has_smoke = any(d.get('label') in SMOKE_LABELS for d in detections)
    eligible = [
        detection for detection in detections
        if qualifies_for_evidence(detection, simulated=simulate_smoke and settings.INFERENCE_MODE == 'demo')
    ]
    model_name = current_model_name()

    alert_id = None
    evidence_url = None
    event = None
    event_created = False
    if source_key:
        event, event_created = track_smoke(
            owner=request.user, source=source, key=source_key, kind=kind, position=position,
            moving=kind != 'image' and request.POST.get('moving') == '1', mode=settings.INFERENCE_MODE,
            model_name=model_name,
            detections=eligible, images=(image, annotated), elapsed_ms=elapsed_ms,
            video_replayed=video_replayed,
        )
        if event:
            alert_id, evidence_url = event['id'], event['url']
    elif save_alert and eligible:
        run = Analysis(
            owner=request.user,
            source=source,
            mode=settings.INFERENCE_MODE,
            model_name=model_name,
            detections=eligible,
            elapsed_ms=elapsed_ms,
        )
        for field, img in [('original', image), ('annotated', annotated)]:
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            getattr(run, field).save(f'{run.id}.jpg', ContentFile(buf.getvalue()), save=False)
        with transaction.atomic():
            run.save()
            alert = Alert.objects.create(analysis=run)
            alert_id = str(alert.id)
        evidence_url = reverse('detail', args=[run.pk])

    return JsonResponse({
        'status': 'success',
        'detections': detections,
        'has_smoke': has_smoke,
        'alert': bool(eligible),
        'alert_id': alert_id,
        'evidence_url': evidence_url,
        'event': event,
        'event_created': event_created,
        'elapsed_ms': elapsed_ms,
        'mode': settings.INFERENCE_MODE,
        'model_name': model_name,
        'image_width': image.width,
        'image_height': image.height,
    })
