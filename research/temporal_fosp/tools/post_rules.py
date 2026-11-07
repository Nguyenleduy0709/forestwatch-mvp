"""Post-processing rules on saved probability maps (tools/predict_masks.py): chosen on val, scored once on test.

R1 new-only  drop a predicted region whose pixels did not change against the pre-ignition reference of the same
             camera (after a global per-channel gain/offset fit, so a lighting change is not a change).  Smoke is
             new; haze bands, glare and bright rocks that the model takes for smoke were already there.
R2 min-area  drop predicted regions smaller than A px (isolated specks).
R3 rescue    add regions above a lower threshold that no region above 0.4 touches, when they changed strongly
             against the reference (faint new smoke the model is unsure of).

Selection, fixed before scoring test (val = 283 smoke images of 5 days + 60 smoke-free frames of the val cameras):
  R1, R2  keep val image-F1 within 0.002 of no rule; among those take the lowest val false-alarm count
          (>= 1 px), then the higher F1.  Adopted when it removes >= 1 val false alarm or gains >= 0.003 F1.
  R3      at most 1 more val false alarm than no rule; take the highest val image-F1.  Adopted when it gains
          >= 0.003.
Only adopted rules are scored on test (701 images of 6 days, 114 smoke-free frames of the test cameras), with
the day-level paired bootstrap against no rule.

--change past    (default) change against the pre-ignition reference, 20-40 min before ignition
--change motion  change against the frame about 2 min earlier (prep_motion.py; 1 min when there is no 2-min
                 frame), so R1 keeps only what moved or grew lately and R3 rescues faint regions that did.
                 Finer grids, fixed from one val camera where smoke changed ~0.02 and the rest ~0.01 in 2 min.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import fw_temporal as fwt  # noqa: E402
from sky_rule import load_prob, load_rgb  # noqa: E402

NEAR = Image.Resampling.NEAREST
THR = 0.4
FAMILIES = {
    'R1_new_only': [('R1', t) for t in (0.01, 0.02, 0.03, 0.05)],
    'R2_min_area': [('R2', a) for a in (10, 30, 100)],
    'R3_rescue': [('R3', (lo, tc)) for lo in (0.2, 0.3) for tc in (0.03, 0.06)],
}
FAMILIES_MOTION = {
    'R1_new_only': [('R1', t) for t in (0.008, 0.012, 0.016, 0.02)],
    'R3_rescue': [('R3', (lo, tc)) for lo in (0.2, 0.3) for tc in (0.015, 0.025)],
}
MOTION = {}


def reference_of(a, r, kind, prep):
    """Image the change is measured against: the pre-ignition reference, or the frame ~2 min earlier."""
    if a.change == 'past':
        return prep / r['ref'] if r.get('ref') else None
    it = MOTION.get(r['id'], {})
    rel = it.get('p2') or it.get('p1')
    return Path(a.motion) / 'fw_motion' / rel if rel else None


def name(cfg):
    return 'none' if cfg is None else f'{cfg[0]}={cfg[1]}'


def change_map(cur, ref):
    """|current - reference| after a global per-channel gain/offset fit, mean over RGB, in [0, 1]."""
    x = ref.reshape(-1, 3).astype(np.float32) / 255.0
    y = cur.reshape(-1, 3).astype(np.float32) / 255.0
    xm, ym = x.mean(0), y.mean(0)
    gain = np.clip(((x - xm) * (y - ym)).mean(0) / (((x - xm) ** 2).mean(0) + 1e-6), 0.25, 4.0)
    r = np.abs(y - (gain * (x - xm) + ym)).mean(1).reshape(cur.shape[:2])
    return cv2.blur(r, (5, 5))


def regions(mask, values):
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    cnt = np.bincount(lab.ravel(), minlength=n)
    mean = np.bincount(lab.ravel(), weights=values.ravel(), minlength=n) / np.maximum(cnt, 1)
    return n, lab, cnt, mean


def apply(cfg, p, chg):
    pred = p > THR
    if cfg is None:
        return pred
    kind, v = cfg
    if kind == 'R2':
        n, lab, cnt, _ = regions(pred, p)
        keep = cnt >= v
        keep[0] = False
        return keep[lab]
    if chg is None:  # no usable reference: the temporal rules leave the prediction as it is
        return pred
    if kind == 'R1':
        n, lab, _, mean = regions(pred, chg)
        keep = mean >= v
        keep[0] = False
        return keep[lab]
    lo, tc = v
    n, lab, cnt, mean = regions(p > lo, chg)
    top = np.zeros(n, np.float32)
    np.maximum.at(top, lab.ravel(), p.ravel())
    add = (top <= THR) & (mean >= tc) & (cnt >= 10)
    add[0] = False
    return pred | add[lab]


def run_pass(a, rows, kind, cfgs):
    prep, seg = Path(a.manifest).parent, Path(a.seg)
    out = {name(c): [] for c in cfgs}
    for r in rows:
        p = load_prob(Path(a.probs) / kind, r['id'])
        chg = None
        ref = reference_of(a, r, kind, prep)
        if ref is not None:
            chg = change_map(load_rgb((prep if kind == 'neg' else seg) / r['image']), load_rgb(ref))
        if kind != 'neg':
            with Image.open(seg / r['mask']) as m:
                gt = np.asarray(m.resize((512, 512), NEAR)).astype(np.int64)
        for c in cfgs:
            pred = apply(c, p, chg)
            if kind == 'neg':
                npx = int(pred.sum())
                out[name(c)].append(dict(id=r['id'], event=r['seq'].split('_')[0], fa_any=float(npx > 0),
                                         fa_50=float(npx >= 50), fa_200=float(npx >= 200)))
            else:
                s = fwt.image_scores(pred, gt == 1, gt == 255)
                s.update(id=r['id'], event=r['event'], offset=r['offset'], size=fwt.size_bucket(s['gt_frac']))
                out[name(c)].append(s)
    return out


def mean(rows, key):
    return float(np.mean([r[key] for r in rows]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--seg', required=True)
    ap.add_argument('--probs', required=True, help='folder written by predict_masks.py')
    ap.add_argument('--change', choices=('past', 'motion'), default='past')
    ap.add_argument('--motion', help='folder holding fw_motion/manifest_motion.json and fw_motion/prev/ (--change motion)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    global FAMILIES
    if a.change == 'motion':
        FAMILIES = FAMILIES_MOTION
        MOTION.update(json.loads((Path(a.motion) / 'fw_motion' / 'manifest_motion.json').read_text())['items'])
        print(json.dumps(dict(motion_items=len(MOTION), with_previous=sum(1 for v in MOTION.values() if 'p1' in v))))
    man = json.loads(Path(a.manifest).read_text())
    negs = man['negatives']
    cfgs = [None] + [c for fam in FAMILIES.values() for c in fam]
    val = run_pass(a, man['val'], 'val', cfgs)
    vneg = run_pass(a, [n for n in negs if n['split'] == 'val'], 'neg', cfgs)
    f0, fa0 = mean(val['none'], 'f1'), sum(r['fa_any'] for r in vneg['none'])
    print(json.dumps(dict(stage='val', rule='none', image_f1=round(f0, 4), val_false_alarms=int(fa0), of=len(vneg['none']))))
    chosen = {}
    for fam, fcfgs in FAMILIES.items():
        rows = []
        for c in fcfgs:
            f, fa = mean(val[name(c)], 'f1'), sum(r['fa_any'] for r in vneg[name(c)])
            rows.append((c, f, fa))
            print(json.dumps(dict(stage='val', rule=name(c), image_f1=round(f, 4), val_false_alarms=int(fa))))
        if fam == 'R3_rescue':
            ok = [x for x in rows if x[2] <= fa0 + 1]
            best = max(ok, key=lambda x: (x[1], -x[2])) if ok else None
            adopt = best is not None and best[1] - f0 >= 0.003
        else:
            ok = [x for x in rows if x[1] >= f0 - 0.002]
            best = min(ok, key=lambda x: (x[2], -x[1])) if ok else None
            adopt = best is not None and (fa0 - best[2] >= 1 or best[1] - f0 >= 0.003)
        print(json.dumps(dict(stage='select', family=fam, best=name(best[0]) if best else None, adopted=bool(adopt))))
        if adopt:
            chosen[fam] = best[0]
    result = dict(val={k: dict(image_f1=mean(v, 'f1')) for k, v in val.items()},
                  val_neg={k: dict(false_alarms=sum(r['fa_any'] for r in v)) for k, v in vneg.items()}, chosen={}, test={})
    if chosen:
        tcfgs = [None] + list(chosen.values())
        test = run_pass(a, man['test'], 'test', tcfgs)
        tneg = run_pass(a, [n for n in negs if n['split'] == 'test'], 'neg', tcfgs)
        for fam, c in chosen.items():
            k = name(c)
            result['chosen'][fam] = k
            for split, rows, key in (('test', test, 'f1'), ('test_neg', tneg, 'fa_any'), ('test_neg', tneg, 'fa_50'),
                                     ('test_neg', tneg, 'fa_200')):
                bs = fwt.paired_bootstrap(rows['none'], rows[k], key=key, group='event')
                line = dict(stage='test', rule=k, split=split, key=key, no_rule=round(mean(rows['none'], key), 4),
                            rule_value=round(mean(rows[k], key), 4), delta=round(bs['delta'], 4),
                            ci95=[round(x, 4) for x in bs['ci95']], days=bs['n_groups'])
                print(json.dumps(line))
                result['test'].setdefault(k, []).append(line)
            for sub, sel in (('small', lambda r: r['size'] == 'small'), ('0-10min', lambda r: r['offset'] < 600)):
                print(json.dumps(dict(stage='test', rule=k, subset=sub, no_rule=round(mean([r for r in test['none'] if sel(r)], 'f1'), 4),
                                      rule_value=round(mean([r for r in test[k] if sel(r)], 'f1'), 4))))
    Path(a.out).write_text(json.dumps(result, indent=1))
    print('wrote', a.out)


if __name__ == '__main__':
    main()
