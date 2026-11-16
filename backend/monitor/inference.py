"""Dispatch demo and trained model inference."""
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from PIL import ImageDraw

EVIDENCE_CONFIDENCE = 0.40
SMOKE_LABELS = {'smoke', 'khoi', 'khói'}


def qualifies_for_evidence(
    detection,
    *,
    simulated=False,
    min_confidence=EVIDENCE_CONFIDENCE,
    min_coverage=0.0,
):
    if detection.get('label') not in SMOKE_LABELS:
        return False
    if simulated:
        return True
    confidence = detection.get('confidence')
    if not (isinstance(confidence, (int, float)) and min_confidence <= confidence <= 1):
        return False
    coverage = detection.get('mask_coverage')
    if coverage is not None and min_coverage and float(coverage) < min_coverage:
        return False
    return True


def apply_exclusion_zones(image, exclusion_zones):
    """Mask configured false-positive exclusion rectangles before AI inference."""
    if not exclusion_zones:
        return image
    masked = image.copy()
    draw = ImageDraw.Draw(masked)
    w, h = masked.size
    for zone in exclusion_zones:
        x1 = int(float(zone.get('x', 0)) * w)
        y1 = int(float(zone.get('y', 0)) * h)
        x2 = int((float(zone.get('x', 0)) + float(zone.get('w', 0))) * w)
        y2 = int((float(zone.get('y', 0)) + float(zone.get('h', 0))) * h)
        if x2 > x1 and y2 > y1:
            draw.rectangle((x1, y1, x2, y2), fill='#15211a')
    return masked


def filter_excluded_detections(detections, exclusion_zones, width, height):
    """Drop detections whose center falls inside any excluded ROI box."""
    if not detections or not exclusion_zones or not width or not height:
        return detections
    kept = []
    for det in detections:
        box = det.get('box')
        if not box or len(box) < 4:
            kept.append(det)
            continue
        cx = ((box[0] + box[2]) / 2.0) / width
        cy = ((box[1] + box[3]) / 2.0) / height
        inside = any(
            float(z.get('x', 0)) <= cx <= float(z.get('x', 0)) + float(z.get('w', 0))
            and float(z.get('y', 0)) <= cy <= float(z.get('y', 0)) + float(z.get('h', 0))
            for z in exclusion_zones
        )
        if not inside:
            kept.append(det)
    return kept


def draw_exclusion_zones(annotated, exclusion_zones):
    """Draw subtle boundary rectangles for ROI exclusion zones on the annotated image."""
    if not exclusion_zones:
        return annotated
    from .fosp import _get_unicode_font
    out = annotated.copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    font, has_unicode = _get_unicode_font(max(12, w // 52))
    for zone in exclusion_zones:
        x1 = int(float(zone.get('x', 0)) * w)
        y1 = int(float(zone.get('y', 0)) * h)
        x2 = int((float(zone.get('x', 0)) + float(zone.get('w', 0))) * w)
        y2 = int((float(zone.get('y', 0)) + float(zone.get('h', 0))) * h)
        if x2 > x1 and y2 > y1:
            draw.rectangle((x1, y1, x2, y2), outline='#38bdf8', width=2)
            label = str(zone.get('label') or ('ROI Loại trừ' if has_unicode else 'ROI Exclusion'))[:24]
            prefix = '[Bỏ qua]' if has_unicode else '[ROI]'
            draw.text((x1 + 6, y1 + 4), f'{prefix} {label}', fill='#38bdf8', font=font)
    return out


def current_model_name():
    if settings.INFERENCE_MODE == 'fosp':
        return settings.FOSP_MODEL_PATH.name
    if settings.INFERENCE_MODE == 'yolo':
        return Path(settings.MODEL_PATH).name
    return ''


@lru_cache(maxsize=1)
def load_model(path):
    if not Path(path).is_file():
        raise RuntimeError('Chưa có trọng số model khói. Đặt best.pt vào weights/ và khởi động lại.')
    try:
        from ultralytics import YOLO
    except ImportError:
        raise RuntimeError('Chưa cài thư viện AI. Chạy: pip install -r requirements-ai.txt')
    model = YOLO(path)
    if str(getattr(model, 'task', '')) != 'detect':
        raise RuntimeError('MVP này cần model object detection, không phải model segmentation/classification.')
    names = {str(v).strip().lower() for v in model.names.values()}
    if not names.intersection(SMOKE_LABELS):
        raise RuntimeError('Model không có lớp smoke. Cần trọng số đã fine-tune cho phát hiện khói rừng.')
    return model

def predict(image, simulate=False, **kwargs):
    mode = settings.INFERENCE_MODE
    width, height = image.size
    if mode == 'demo':
        detections = []
        if simulate:
            detections.append({'label': 'smoke', 'confidence': None,
                               'box': [int(width*.36), int(height*.17), int(width*.67), int(height*.66)]})
    elif mode == 'yolo':
        model = load_model(settings.MODEL_PATH)
        result = model.predict(image, conf=settings.MODEL_CONFIDENCE, verbose=False)[0]
        detections = []
        for box in result.boxes:
            label = str(result.names[int(box.cls.item())]).lower().strip()
            if label in SMOKE_LABELS:
                detections.append({'label': label, 'confidence': float(box.conf.item()),
                                   'box': [round(float(v)) for v in box.xyxy[0].tolist()]})
    elif mode == 'fosp':
        from .fosp import predict_fosp
        reference = kwargs.get('reference')
        return predict_fosp(image, reference) if reference is not None else predict_fosp(image)
    else:
        raise RuntimeError('INFERENCE_MODE chỉ nhận demo, yolo hoặc fosp.')
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    for item in detections:
        box_color = '#ffb44a'
        text_color = '#142b23'

        draw.rectangle(item['box'], outline=box_color, width=max(3, width//350))
        label = f"SIMULATION - {item['label']}" if mode == 'demo' else f"{item['label']} {item['confidence']:.0%}"
        x, y = item['box'][:2]
        draw.rectangle((x, max(0, y-25), min(width, x+240), y), fill=box_color)
        draw.text((x+5, max(0, y-21)), label, fill=text_color)
    return detections, annotated
