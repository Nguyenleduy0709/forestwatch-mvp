"""Paired bootstrap between runs stored in different per_image.json files (same test images, same negatives).
Usage: python -I research/temporal_fosp/tools/compare_runs.py A=<per_image.json>:<run> B=<per_image.json>:<run> [...]
Each later run is compared with the first one: test image-F1 / image-IoU (higher is better) and false alarms on
the real smoke-free frames, fa_any / fa_200 (lower is better).  Groups = days (events), as in train_temporal.py.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fw_temporal as fwt  # noqa: E402

runs = []
for arg in sys.argv[1:]:
    label, _, spec = arg.partition('=')
    path, _, run = spec.rpartition(':')
    runs.append((label, json.loads(Path(path).read_text(encoding='utf-8'))[run]))


def by_id(rows):
    return {r['id']: r for r in rows}


base_label, base = runs[0]
for label, other in runs[1:]:
    for split, keys in (('test', ('f1', 'iou')), ('neg', ('fa_any', 'fa_200')), ('s5k', ('f1', 'iou')), ('s5k_cam', ('f1',))):
        src = 's5k' if split == 's5k_cam' else split  # s5k_cam = the 63 fixed-camera frames of the SMOKE5K test
        if src not in base or src not in other:
            continue
        a, b = by_id(base[src]), by_id(other[src])
        ids = sorted(i for i in set(a) & set(b) if split != 's5k_cam' or a[i].get('cam'))
        ra, rb = [a[i] for i in ids], [b[i] for i in ids]
        for key in keys:
            res = fwt.paired_bootstrap(ra, rb, key=key, group='event')
            mean_a = sum(float(r[key]) for r in ra) / len(ra)
            mean_b = sum(float(r[key]) for r in rb) / len(rb)
            print(f'{label} - {base_label}  {split:7s} {key:6s}  {mean_a:.4f} -> {mean_b:.4f}  '
                  f'delta {res["delta"]:+.4f}  CI95 [{res["ci95"][0]:+.4f}, {res["ci95"][1]:+.4f}]  '
                  f'days {res["n_groups"]}  P(delta<=0) {res["p_le_0"]:.3f}  n={len(ids)}')
