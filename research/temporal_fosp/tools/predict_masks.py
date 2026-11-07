"""Save probability maps of a single-frame model (E0 FoSp or an E1 checkpoint) for val, test and the smoke-free frames.

Follows train_temporal.evaluate: image resized to 512x512 (bilinear), FoSp normalisation, MAT latent z = 0,
logits upsampled to 512x512, smoke where p > threshold.  Maps are written as 16-bit PNG (p * 65535) so that
post-processing rules can be tried without the GPU; scores at the threshold go to <out>/per_image.json in the
layout of train_temporal (split -> rows), to check against the Kaggle numbers of the same checkpoint.

  python -I research/temporal_fosp/tools/predict_masks.py --fosp <FoSp> --mmcv <mmcv> --ckpt <E1_single_best.pth> \
      --manifest <fw_day2/manifest_day2.json> --seg <SmokeSeg folder with JPEGImages/> --out work/cloud_rule/B
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--fosp', required=True, help='FoSp-official checkout')
ap.add_argument('--mmcv', required=True, help='mmcv 1.6.2 source folder')
ap.add_argument('--ckpt', required=True, help='TemporalFoSp *_best.pth (E1) or a plain FoSp best.pth (E0)')
ap.add_argument('--manifest', required=True)
ap.add_argument('--seg', required=True, help='SmokeSeg folder holding JPEGImages/ and SegmentationClass/')
ap.add_argument('--out', required=True)
ap.add_argument('--splits', default='val,test,neg')
ap.add_argument('--batch', type=int, default=4)
ap.add_argument('--threshold', type=float, default=0.4)
ap.add_argument('--limit', type=int, default=0, help='first N rows per split (quick check)')
a = ap.parse_args()

sys.path[:0] = [a.fosp, str(Path(a.fosp) / 'inpainter_setting'), a.mmcv]
from mmcv import Config  # noqa: E402
from mmseg.models import build_segmentor  # noqa: E402
from inpainter_setting.torch_utils.ops import bias_act, upfirdn2d  # noqa: E402
from torch_utils.ops import bias_act as ba, upfirdn2d as uf  # noqa: E402
import fw_temporal as fwt  # noqa: E402

OPS = (bias_act, upfirdn2d, ba, uf)
for op in OPS:
    op._init = lambda: False  # reference implementation of MAT's custom ops, as in train_temporal
BIL, NEAR = Image.Resampling.BILINEAR, Image.Resampling.NEAREST

cfg = Config.fromfile(str(Path(a.fosp) / 'config/fosp_config.py'))
cfg.model.pretrained = None
seg = build_segmentor(cfg.model).cuda()
for op in OPS:
    op._init = lambda: False
state = torch.load(a.ckpt, map_location='cpu', weights_only=True)
model = fwt.TemporalFoSp(seg, num_refs=1, use_temporal=False).cuda()
sd = state['state_dict']
if any(k.startswith('seg.') for k in sd):
    assert not state.get('use_temporal'), 'only single-frame checkpoints (E0/E1)'
    model.load_state_dict(sd, strict=True)
else:
    seg.load_state_dict(sd, strict=True)
del state, sd
model.eval()

MF = Path(a.manifest)
PREP, SEG = MF.parent, Path(a.seg)
man = json.loads(MF.read_text())
ROWS = {'val': man['val'], 'test': man['test'], 'neg': [n for n in man['negatives'] if n['split'] in ('val', 'test')]}


def load_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB').resize((512, 512), BIL), np.uint8)


def load_mask(path):
    with Image.open(path) as m:
        return np.asarray(m.resize((512, 512), NEAR)).astype(np.int64)


out = Path(a.out)
scores = {}
t0 = time.time()
for split in a.splits.split(','):
    rows = ROWS[split][:a.limit] if a.limit else ROWS[split]
    folder = out / split
    folder.mkdir(parents=True, exist_ok=True)
    res = []
    for i in range(0, len(rows), a.batch):
        chunk = rows[i:i + a.batch]
        imgs = [load_rgb((PREP if split == 'neg' else SEG) / r['image']) for r in chunk]
        x = torch.from_numpy(np.stack([((im.astype(np.float32) - fwt.MEAN) / fwt.STD).transpose(2, 0, 1) for im in imgs])).cuda()
        with torch.no_grad():
            logits, _, _ = model(x, None, torch.ones(len(chunk), device='cuda'))
            prob = torch.sigmoid(F.interpolate(logits.float(), size=(512, 512), mode='bilinear', align_corners=False))[:, 0]
        prob = prob.cpu().numpy()
        for r, p in zip(chunk, prob):
            Image.fromarray(np.round(p * 65535).astype(np.uint16)).save(folder / f"{r['id']}.png")
            pred = p > a.threshold
            if split == 'neg':
                npx = int(pred.sum())
                res.append(dict(id=r['id'], event=r['seq'].split('_')[0], seq=r['seq'], area=npx / pred.size,
                                fa_any=npx > 0, fa_50=npx >= 50, fa_200=npx >= 200))
            else:
                gt = load_mask(SEG / r['mask'])
                s = fwt.image_scores(pred, gt == 1, gt == 255)
                s.update(id=r['id'], event=r['event'], seq=r['seq'], offset=r['offset'], size=fwt.size_bucket(s['gt_frac']))
                res.append(s)
    scores[split] = res
    if split == 'neg':
        print(split, len(res), 'fa_any', round(float(np.mean([r['fa_any'] for r in res])), 4),
              'fa_200', round(float(np.mean([r['fa_200'] for r in res])), 4), f'{time.time() - t0:.0f}s', flush=True)
    else:
        print(split, len(res), 'image_f1', round(float(np.mean([r['f1'] for r in res])), 4), f'{time.time() - t0:.0f}s', flush=True)
(out / 'per_image.json').write_text(json.dumps(scores))
print('done', f'{time.time() - t0:.0f}s')
