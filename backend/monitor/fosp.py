"""FoSp smoke-mask inference using the architecture and preprocessing from training."""
import sys
from functools import lru_cache
from importlib import import_module
from threading import Lock

from django.conf import settings
from PIL import Image

_gpu_lock = Lock()


def preload_torch_on_windows():
    if sys.platform != 'win32' or settings.INFERENCE_MODE != 'fosp':
        return
    # Initialize the native runtime before Django starts its request threads.
    try:
        import_module('torch')
    except (ImportError, OSError):
        # Inference reports missing or broken model dependencies on the request.
        pass


@lru_cache(maxsize=1)
def _load_model(weights, runtime):
    if not weights.is_file():
        raise RuntimeError(f'Thiếu checkpoint {weights.name}. Kiểm tra FOSP_MODEL_PATH.')
    source = runtime / 'FoSp'
    config_path = source / 'config' / 'fosp_config.py'
    if not config_path.is_file() or not (runtime / 'mmcv' / 'mmcv').is_dir():
        raise RuntimeError('Thiếu mã nguồn FoSp hoặc MMCV. Xem hướng dẫn FoSp trong README.')
    try:
        import torch

        sys.path[:0] = [str(path.resolve()) for path in (source, source / 'inpainter_setting', runtime / 'mmcv')]
        from mmcv import Config
        from mmseg.models import build_segmentor
        from inpainter_setting.torch_utils.ops import bias_act, upfirdn2d
        from torch_utils.ops import bias_act as mat_bias_act, upfirdn2d as mat_upfirdn2d
    except ImportError as exc:
        raise RuntimeError('Thiếu thư viện FoSp. Cài backend/requirements-fosp.txt.') from exc

    # Use MAT's reference operations as in the Kaggle training environment.
    for operation in (bias_act, upfirdn2d, mat_bias_act, mat_upfirdn2d):
        operation._init = lambda: False
    config = Config.fromfile(str(config_path))
    config.model.pretrained = None
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = build_segmentor(config.model).to(device)
    checkpoint = torch.load(weights, map_location='cpu', weights_only=True)
    state_dict = checkpoint.get('state_dict', checkpoint)
    if checkpoint.get('input_fusion') == 'concat' and checkpoint.get('use_temporal') is False:
        from .fosp_concat import FoSpConcat

        model = FoSpConcat(model).to(device)
        try:
            model.load_state_dict(state_dict, strict=True)
        except RuntimeError as exc:
            raise RuntimeError(f'Trọng số {weights.name} không khớp kiến trúc FoSp E3 concat.') from exc
        return model.eval(), float(checkpoint['cfg']['threshold']), True
    if 'extra_stem.weight' in state_dict or any(key.startswith('arbitration.') for key in state_dict):
        raise RuntimeError(f'Checkpoint {weights.name} cần bộ chạy FoSp khác.')
    cleaned = {}
    for k, v in state_dict.items():
        # Remap generator prefix from Kaggle/research training checkpoints
        cleaned[k.replace('decode_head.G.generator.', 'decode_head.G.')] = v
    model.load_state_dict(cleaned, strict=False)
    return model.eval(), 0.4, False


def predict_fosp(image, reference=None):
    with _gpu_lock:
        model, threshold, concat = _load_model(settings.FOSP_MODEL_PATH, settings.FOSP_RUNTIME_DIR)
        import numpy as np
        import torch
        from torch.nn.functional import interpolate

        device = next(model.parameters()).device
        image = image.convert('RGB')
        mean = np.array([123.675, 116.28, 103.53], dtype=np.float32)
        std = np.array([58.395, 57.12, 57.375], dtype=np.float32)

        def model_input(frame):
            pixels = np.asarray(frame.resize((512, 512), Image.Resampling.BILINEAR), dtype=np.float32)
            return torch.from_numpy(((pixels - mean) / std).transpose(2, 0, 1).copy()).unsqueeze(0).to(device)

        tensor = model_input(image)
        background = model_input(reference.convert('RGB')) if concat and reference is not None else None
        # MAT samples a latent from NumPy. Seed per request so repeat uploads agree.
        numpy_state = np.random.get_state()
        try:
            np.random.seed(123)
            with torch.no_grad():
                logits = model(tensor, background) if concat else model.encode_decode(tensor, [{}])
                logits = interpolate(logits, size=(image.height, image.width), mode='bilinear', align_corners=False)
                probabilities = logits[0, 0].sigmoid().cpu().numpy()
                mask = probabilities >= threshold
        finally:
            np.random.set_state(numpy_state)

    if not mask.any():
        return [], image

    import cv2
    from PIL import ImageDraw

    # Generate colored semi-transparent mask overlay
    alpha = Image.fromarray((mask * 140).astype('uint8'))
    overlay = Image.composite(Image.new('RGB', image.size, '#ff8800'), image, alpha)

    # Calculate bounding boxes and contours from the predicted mask.
    uint_mask = (mask * 255).astype(np.uint8)
    contours, _ = cv2.findContours(uint_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Filter out tiny noise spots (< 0.05% of image area or < 50 px)
    min_area = max(50, int(image.width * image.height * 0.0005))
    valid_contours = [c for c in contours if cv2.contourArea(c) >= min_area]

    detections = []
    draw = ImageDraw.Draw(overlay)

    if valid_contours:
        valid_contours.sort(key=cv2.contourArea, reverse=True)
        for cnt in valid_contours:
            x, y, w, h = cv2.boundingRect(cnt)
            cnt_coverage = float((cv2.contourArea(cnt) / (image.width * image.height)) * 100)

            # Approximate contour to polygon
            epsilon = 0.012 * cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, epsilon, True)
            poly = approx.reshape(-1, 2).tolist()

            box = [int(x), int(y), int(x + w), int(y + h)]
            region = np.zeros((h, w), dtype=np.uint8)
            cv2.drawContours(region, [cnt], -1, 1, cv2.FILLED, offset=(-x, -y))
            confidence = float(probabilities[y:y + h, x:x + w][region.astype(bool)].max())
            detections.append({
                'label': 'smoke',
                'confidence': confidence,
                'mask_coverage': round(cnt_coverage, 2),
                'box': box,
                'polygon': poly,
            })
            draw.rectangle(box, outline='#ff9900', width=max(3, image.width // 350))
            label_text = f"Khói ({round(cnt_coverage, 1)}%)"
            draw.rectangle((x, max(0, y - 24), min(image.width, x + 150), y), fill='#ff9900')
            draw.text((x + 5, max(0, y - 20)), label_text, fill='#142b23')
    else:
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        ymin, ymax = np.where(rows)[0][[0, -1]]
        xmin, xmax = np.where(cols)[0][[0, -1]]
        box = [int(xmin), int(ymin), int(xmax), int(ymax)]
        total_cov = round(float(mask.mean() * 100), 2)
        detections.append({
            'label': 'smoke',
            'confidence': float(probabilities[mask].max()),
            'mask_coverage': total_cov,
            'box': box,
        })
        draw.rectangle(box, outline='#ff9900', width=max(3, image.width // 350))
        label_text = f"Khói ({total_cov}%)"
        draw.rectangle((xmin, max(0, ymin - 24), min(image.width, xmin + 150), ymin), fill='#ff9900')
        draw.text((xmin + 5, max(0, ymin - 20)), label_text, fill='#142b23')

    return detections, overlay
