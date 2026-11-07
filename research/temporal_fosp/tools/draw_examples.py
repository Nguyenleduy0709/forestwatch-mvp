"""Side-by-side test examples: image, label, original FoSp (E0), chosen model (B), on samples fixed by a hash of their id.

The samples are the first N test rows sorted by md5(id), the rule train_temporal.py uses for FW_EXAMPLES, so they are
not picked by score.  Probability maps come from tools/predict_masks.py (one folder per model).

  python -I research/temporal_fosp/tools/draw_examples.py --manifest <manifest_day2.json> --seg <SmokeSeg> \
      --probs E0=work/cloud_rule/E0 --probs B=work/cloud_rule/B --n 6 --out work/figures/examples_e0_b.png
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fw_temporal as fwt  # noqa: E402

GT_EDGE = (1.0, 0.83, 0.0)
PRED = np.array([0.9, 0.1, 0.1])


def load_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB').resize((512, 512), Image.Resampling.BILINEAR), np.float32) / 255.0


def load_prob(folder, rid):
    with Image.open(Path(folder) / 'test' / f'{rid}.png') as im:
        return np.asarray(im, np.uint16).astype(np.float32) / 65535.0


def outline(mask):
    m = mask.astype(bool)
    edge = np.zeros_like(m)
    edge[1:-1, 1:-1] = m[1:-1, 1:-1] & ~(m[:-2, 1:-1] & m[2:, 1:-1] & m[1:-1, :-2] & m[1:-1, 2:])
    return edge


def crop_box(gt, preds, size=176):
    ys, xs = np.nonzero(gt | np.any(preds, axis=0))
    if len(ys) == 0:
        cy = cx = 256
    else:
        cy, cx = int((ys.min() + ys.max()) / 2), int((xs.min() + xs.max()) / 2)
        size = int(min(512, max(size, 1.3 * (ys.max() - ys.min()), 1.3 * (xs.max() - xs.min()))))
    y0 = int(np.clip(cy - size // 2, 0, 512 - size))
    x0 = int(np.clip(cx - size // 2, 0, 512 - size))
    return y0, x0, size


def tile(img, gt, pred=None):
    t = img.copy()
    if pred is not None:
        t[pred] = 0.45 * t[pred] + 0.55 * PRED
    t[outline(gt) | outline(np.pad(gt, 1)[1:-1, 1:-1])] = GT_EDGE
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--seg', required=True)
    ap.add_argument('--probs', action='append', required=True, metavar='NAME=FOLDER')
    ap.add_argument('--labels', default='FoSp gốc,B (model được chọn)')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--threshold', type=float, default=0.4)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    man = json.loads(Path(a.manifest).read_text())
    seg = Path(a.seg)
    models = [p.split('=', 1) for p in a.probs]
    labels = a.labels.split(',')
    rows = sorted(man['test'], key=lambda r: hashlib.md5(r['id'].encode()).hexdigest())[:a.n]
    fig, axes = plt.subplots(len(rows), 2 + len(models), figsize=(1.95 * (2 + len(models)), 1.95 * len(rows) + 0.35))
    heads = ['Ảnh gốc', 'Nhãn (viền vàng)'] + labels
    for j, h in enumerate(heads):
        axes[0, j].set_title(h, fontsize=10)
    for i, r in enumerate(rows):
        img = load_rgb(seg / r['image'])
        with Image.open(seg / r['mask']) as m:
            lab = np.asarray(m.resize((512, 512), Image.Resampling.NEAREST)).astype(np.int64)
        gt = lab == 1
        preds = np.stack([load_prob(f, r['id']) > a.threshold for _, f in models])
        y0, x0, s = crop_box(gt, preds)
        sl = (slice(y0, y0 + s), slice(x0, x0 + s))
        axes[i, 0].imshow(img)
        axes[i, 0].add_patch(Rectangle((x0, y0), s, s, fill=False, ec='red', lw=1.4))
        size = fwt.size_bucket(gt.mean())
        axes[i, 0].set_ylabel(f"{r['offset'] // 60} phút sau khi cháy\nkhói {dict(small='nhỏ', medium='vừa', large='lớn')[size]}",
                              fontsize=8.5, rotation=0, ha='right', va='center', labelpad=4)
        axes[i, 1].imshow(tile(img, gt)[sl])
        for k, (name, _) in enumerate(models):
            s_ = fwt.image_scores(preds[k], gt, lab == 255)
            axes[i, 2 + k].imshow(tile(img, gt, preds[k])[sl])
            axes[i, 2 + k].text(0.03, 0.04, f"F1 {s_['f1']:.2f}".replace('.', ','), transform=axes[i, 2 + k].transAxes, fontsize=9,
                                color='white', bbox=dict(boxstyle='round,pad=0.2', fc='black', alpha=0.6, lw=0))
        for ax in axes[i]:
            ax.set_xticks([])
            ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_linewidth(0.5)
                sp.set_color('#888888')
    fig.text(0.5, 0.005, 'Mẫu lấy theo mã băm của tên ảnh, không chọn theo điểm. Khung đỏ ở cột đầu là vùng được phóng to ở các cột sau.\n'
             'Đỏ: vùng model báo khói, ngưỡng 0,4. Viền vàng: nhãn.', ha='center', fontsize=8.5, color='#444444')
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(a.out, dpi=170, facecolor='white')
    print('wrote', a.out, [r['id'] for r in rows])


if __name__ == '__main__':
    main()
