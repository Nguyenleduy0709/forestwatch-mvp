"""Ground-contact rule: a smoke region must reach the terrain, a region that lies wholly in the sky is dropped.

The sky comes from the pre-ignition reference of the same fixed camera (median of the frames 20-40 min before
ignition, registered to the image): smooth, untextured pixels connected to the top of the frame.  Smoke rises
from vegetation, so a plume touches the terrain or the skyline; a cloud does not.  A predicted connected
component is kept when it comes within `margin` px of the terrain (on or below the skyline).

  sheet  draw the sky found on the references of the given splits (to check the detector by eye)
  eval   apply the rule to saved probability maps (tools/predict_masks.py), choose the margin on val,
         then score test and the smoke-free frames once, with day-level paired bootstrap against no rule
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fw_temporal as fwt  # noqa: E402

BIL, NEAR = Image.Resampling.BILINEAR, Image.Resampling.NEAREST
# Detector settings, fixed on the val references before any test scoring.
TEX_BOX = 11          # window of the local texture (mean Sobel magnitude)
TEX_REL = 0.35        # sky: texture below TEX_REL x the median texture of the lowest quarter of the frame
TEX_ABS = 0.012       # ... and below this absolute level (intensity in [0, 1] per pixel)
TOP_ROWS = 48         # sky components must touch the top TOP_ROWS rows (text overlays sit on row 0)
MIN_SKY = 0.02        # less sky than this share of the frame: treat the whole frame as terrain
MARGINS = (0, 8, 16, 32, 64)


def load_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB').resize((512, 512), BIL), np.uint8)


CLOUD_V, CLOUD_SAT = 0.70, 0.25   # 'clouds' variant: bright, unsaturated pixels also count as sky


def sky_mask(ref, clouds=False):
    """Boolean sky mask and per-column skyline (first terrain row) of a 512x512 RGB reference.

    Plain variant: smooth pixels connected to the top (clear sky).  With clouds=True, bright unsaturated pixels
    (cloud tops) may also carry the sky region, so a cloud bank does not end the sky early."""
    rgb = ref.astype(np.float32) / 255.0
    g = cv2.GaussianBlur(rgb.mean(2), (0, 0), 1.0)
    mag = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    tex = cv2.blur(mag, (TEX_BOX, TEX_BOX))
    h, w = g.shape
    ground_tex = float(np.median(tex[3 * h // 4:]))
    cand = tex < min(TEX_REL * ground_tex, TEX_ABS)
    if clouds:
        v = cv2.GaussianBlur(rgb.max(2), (0, 0), 1.0)
        sat = cv2.GaussianBlur((rgb.max(2) - rgb.min(2)) / np.maximum(rgb.max(2), 1e-3), (0, 0), 1.0)
        cand |= (v >= CLOUD_V) & (sat <= CLOUD_SAT)
    cand = cand.astype(np.uint8)
    n, lab = cv2.connectedComponents(cand, connectivity=4)
    top = set(np.unique(lab[:TOP_ROWS][cand[:TOP_ROWS] > 0]).tolist()) - {0}
    sky = np.isin(lab, list(top)) if top else np.zeros_like(cand, bool)
    sky = cv2.morphologyEx(sky.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8)) > 0
    # skyline: per column, the lowest sky row; everything below it is terrain (sky holes become sky)
    has = sky.any(0)
    low = np.where(has, h - 1 - np.argmax(sky[::-1], 0), -1)
    low = np.array([int(np.median(low[max(0, x - 15):x + 16])) for x in range(w)])
    rows = np.arange(h)[:, None]
    sky = rows <= low[None, :]
    if sky.mean() < MIN_SKY:
        sky[:] = False
        low[:] = -1
    return sky, low + 1


def keep_grounded(pred, skyline, margin):
    """Drop connected components of `pred` that stay more than `margin` px above the skyline everywhere."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(pred.astype(np.uint8), connectivity=8)
    if n <= 1:
        return pred
    rows = np.arange(pred.shape[0])[:, None]
    ground = rows >= (skyline[None, :] - margin)
    touching = np.unique(lab[ground & (lab > 0)])
    return np.isin(lab, touching) & pred


def sheet(a):
    man = json.loads(Path(a.manifest).read_text())
    prep = Path(a.manifest).parent
    tiles = []
    for split in a.splits.split(','):
        seen, picked = set(), []
        for r in man[split]:
            if r.get('ref') and r['seq'] not in seen:
                seen.add(r['seq'])
                picked.append(r)
        if a.cloudiest:  # the references with the largest share of bright unsaturated pixels in the top half
            def cloudiness(r):
                x = load_rgb(prep / r['ref'])[:256].astype(np.float32) / 255.0
                return float(((x.max(2) >= CLOUD_V) & ((x.max(2) - x.min(2)) / np.maximum(x.max(2), 1e-3) <= CLOUD_SAT)).mean())
            picked = sorted(picked, key=cloudiness, reverse=True)[:a.cloudiest]
        for r in picked:
            if True:
                ref = load_rgb(prep / r['ref'])
                sky, line = sky_mask(ref, a.clouds)
                t = ref.copy()
                t[sky] = (0.55 * t[sky] + 0.45 * np.array([60, 140, 255])).astype(np.uint8)
                for x in range(512):
                    if 0 < line[x] < 512:
                        t[max(0, line[x] - 1):line[x] + 2, x] = (255, 40, 40)
                cv2.putText(t, f'{split} {r["seq"][:30]}', (6, 500), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                tiles.append(cv2.resize(t, (256, 256), interpolation=cv2.INTER_AREA))
    cols = 6
    rows = [np.concatenate(tiles[i:i + cols] + [np.zeros_like(tiles[0])] * (cols - len(tiles[i:i + cols])), 1)
            for i in range(0, len(tiles), cols)]
    Image.fromarray(np.concatenate(rows, 0)).save(a.out)
    print('wrote', a.out, len(tiles), 'references')


def load_prob(folder, rid):
    with Image.open(folder / f'{rid}.png') as im:
        return np.asarray(im, np.uint16).astype(np.float32) / 65535.0


def score_split(a, man, prep, seg, split, margin):
    rows = man[split] if split != 'neg' else [n for n in man['negatives'] if n['split'] in ('val', 'test')]
    out = []
    for r in rows:
        p = load_prob(Path(a.probs) / split, r['id']) > a.threshold
        if margin is not None and r.get('ref'):
            _, line = sky_of(prep, r['ref'])
            p = keep_grounded(p, line, margin)
        if split == 'neg':
            npx = int(p.sum())
            out.append(dict(id=r['id'], event=r['seq'].split('_')[0], fa_any=float(npx > 0), fa_50=float(npx >= 50),
                            fa_200=float(npx >= 200)))
        else:
            with Image.open(seg / r['mask']) as m:
                gt = np.asarray(m.resize((512, 512), NEAR)).astype(np.int64)
            s = fwt.image_scores(p, gt == 1, gt == 255)
            s.update(id=r['id'], event=r['event'], offset=r['offset'], size=fwt.size_bucket(s['gt_frac']))
            out.append(s)
    return out


SKY = {}


def sky_of(prep, ref):
    """Sky of a reference with the detector fixed on train/val references: the 'clouds' variant."""
    if ref not in SKY:
        SKY[ref] = sky_mask(load_rgb(prep / ref), clouds=True)
    return SKY[ref]


def diagnose(a, man, prep, seg):
    """Where the errors of the model (no rule) sit: sky or terrain pixels of the reference."""
    c = dict(test_fp_sky=0, test_fp_ground=0, test_tp_sky=0, test_tp_ground=0, neg_fa_sky=0, neg_fa_ground=0,
             neg_frames_fa=0, neg_frames_fa_only_sky=0, neg_frames_fa200=0, neg_frames_fa200_only_sky=0)
    for r in man['test']:
        if not r.get('ref'):
            continue
        p = load_prob(Path(a.probs) / 'test', r['id']) > a.threshold
        sky, _ = sky_of(prep, r['ref'])
        with Image.open(seg / r['mask']) as m:
            gt = np.asarray(m.resize((512, 512), NEAR)).astype(np.int64)
        fp, tp = p & (gt == 0), p & (gt == 1)
        c['test_fp_sky'] += int((fp & sky).sum()); c['test_fp_ground'] += int((fp & ~sky).sum())
        c['test_tp_sky'] += int((tp & sky).sum()); c['test_tp_ground'] += int((tp & ~sky).sum())
    for r in [n for n in man['negatives'] if n['split'] in ('val', 'test')]:
        p = load_prob(Path(a.probs) / 'neg', r['id']) > a.threshold
        sky, _ = sky_of(prep, r['ref'])
        s, g = int((p & sky).sum()), int((p & ~sky).sum())
        c['neg_fa_sky'] += s; c['neg_fa_ground'] += g
        c['neg_frames_fa'] += int(s + g > 0); c['neg_frames_fa_only_sky'] += int(s > 0 and g == 0)
        c['neg_frames_fa200'] += int(s + g >= 200); c['neg_frames_fa200_only_sky'] += int(s + g >= 200 and g == 0)
    print(json.dumps(dict(diagnosis=c)))


def evaluate(a):
    man = json.loads(Path(a.manifest).read_text())
    prep, seg = Path(a.manifest).parent, Path(a.seg)
    diagnose(a, man, prep, seg)
    res = {'no_rule': {s: score_split(a, man, prep, seg, s, None) for s in ('val', 'test', 'neg')}}
    val = {}
    for m in MARGINS:
        val[m] = score_split(a, man, prep, seg, 'val', m)
        print(json.dumps(dict(split='val', margin=m, image_f1=round(float(np.mean([r['f1'] for r in val[m]])), 4))))
    print(json.dumps(dict(split='val', margin=None, image_f1=round(float(np.mean([r['f1'] for r in res['no_rule']['val']])), 4))))
    best = max(MARGINS, key=lambda m: (round(float(np.mean([r['f1'] for r in val[m]])), 3), m))  # ties -> larger margin
    print(json.dumps(dict(chosen_margin=best)))
    res['rule'] = {'val': val[best], 'test': score_split(a, man, prep, seg, 'test', best),
                   'neg': score_split(a, man, prep, seg, 'neg', best)}
    for split, key in (('val', 'f1'), ('test', 'f1'), ('neg', 'fa_any'), ('neg', 'fa_50'), ('neg', 'fa_200')):
        a_rows, b_rows = res['no_rule'][split], res['rule'][split]
        bs = fwt.paired_bootstrap(a_rows, b_rows, key=key, group='event')
        print(json.dumps(dict(split=split, key=key, no_rule=round(float(np.mean([r[key] for r in a_rows])), 4),
                              rule=round(float(np.mean([r[key] for r in b_rows])), 4), delta=round(bs['delta'], 4),
                              ci95=[round(c, 4) for c in bs['ci95']], days=bs['n_groups'])))
    t = res['rule']['test']
    for name, sel in (('small', lambda r: r['size'] == 'small'), ('0-10min', lambda r: r['offset'] < 600)):
        b = [r for r in t if sel(r)]
        a0 = [r for r in res['no_rule']['test'] if sel(r)]
        print(json.dumps(dict(split='test', subset=name, n=len(b), no_rule=round(float(np.mean([r['f1'] for r in a0])), 4),
                              rule=round(float(np.mean([r['f1'] for r in b])), 4))))
    Path(a.out).write_text(json.dumps(dict(margin=best, **res)))
    print('wrote', a.out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=('sheet', 'eval'))
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--seg', help='SmokeSeg folder (eval)')
    ap.add_argument('--probs', help='folder written by predict_masks.py (eval)')
    ap.add_argument('--splits', default='val')
    ap.add_argument('--threshold', type=float, default=0.4)
    ap.add_argument('--clouds', action='store_true', help='sheet: draw the variant that lets clouds carry the sky')
    ap.add_argument('--cloudiest', type=int, default=0, help='sheet: only the N cloudiest references of the splits')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    sheet(a) if a.mode == 'sheet' else evaluate(a)


if __name__ == '__main__':
    main()
