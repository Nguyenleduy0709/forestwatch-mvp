"""FoSp smoke-mask inference using the architecture and preprocessing from training."""
import sys
from functools import lru_cache
from threading import Lock

from django.conf import settings
from PIL import Image

_gpu_lock = Lock()


@lru_cache(maxsize=1)
def _load_model(weights, runtime):
    if not weights.is_file():
        raise RuntimeError('Thiếu trọng số FoSp. Đặt best.pth vào backend/weights/fosp_best.pth.')
    source = runtime / 'FoSp'
    config_path = source / 'config' / 'fosp_config.py'
    if not config_path.is_file() or not (runtime / 'mmcv' / 'mmcv').is_dir():
        raise RuntimeError('Thiếu mã nguồn FoSp hoặc MMCV. Xem hướng dẫn FoSp trong README.')
    try:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError('FoSp cần GPU NVIDIA và PyTorch có CUDA.')
        sys.path[:0] = [str(path.resolve()) for path in (source, source / 'inpainter_setting', runtime / 'mmcv')]
        from mmcv import Config
        from mmseg.models import build_segmentor
        from inpainter_setting.torch_utils.ops import bias_act, upfirdn2d
        from torch_utils.ops import bias_act as mat_bias_act, upfirdn2d as mat_upfirdn2d
    except ImportError as exc:
        raise RuntimeError('Thiếu thư viện FoSp. Cài backend/requirements-fosp.txt và PyTorch CUDA.') from exc

    # Use MAT's reference operations as in the Kaggle training environment.
    for operation in (bias_act, upfirdn2d, mat_bias_act, mat_upfirdn2d):
        operation._init = lambda: False
    config = Config.fromfile(str(config_path))
    config.model.pretrained = None
    model = build_segmentor(config.model).cuda()
    checkpoint = torch.load(weights, map_location='cpu', weights_only=True)
    model.load_state_dict(checkpoint['state_dict'], strict=True)
    return model.eval()


def predict_fosp(image):
    with _gpu_lock:
        model = _load_model(settings.FOSP_MODEL_PATH, settings.FOSP_RUNTIME_DIR)
        import numpy as np
        import torch
        from torch.nn.functional import interpolate

        image = image.convert('RGB')
        pixels = np.asarray(image.resize((512, 512), Image.Resampling.BILINEAR), dtype=np.float32)
        mean = np.array([123.675, 116.28, 103.53], dtype=np.float32)
        std = np.array([58.395, 57.12, 57.375], dtype=np.float32)
        tensor = torch.from_numpy(((pixels - mean) / std).transpose(2, 0, 1).copy())
        # MAT samples a latent from NumPy. Seed per request so repeat uploads agree.
        numpy_state = np.random.get_state()
        try:
            np.random.seed(123)
            with torch.no_grad():
                logits = model.encode_decode(tensor.unsqueeze(0).cuda(), [{}])
                logits = interpolate(logits, size=(image.height, image.width), mode='bilinear', align_corners=False)
                mask = (logits[0, 0].sigmoid() > 0.4).cpu().numpy()
        finally:
            np.random.set_state(numpy_state)

    if not mask.any():
        return [], image
    alpha = Image.fromarray(mask.astype('uint8') * 128)
    overlay = Image.composite(Image.new('RGB', image.size, '#ffa52d'), image, alpha)
    return [{'label': 'smoke', 'mask_coverage': float(mask.mean() * 100)}], overlay
