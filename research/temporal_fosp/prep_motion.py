"""ForestWatch Day 5 (CPU): short-term history for a motion cue (about 1 and 2 min before each frame).

For every registered SmokeSeg image (train/val/test) and every smoke-free frame of the val/test cameras,
download the two FIgLib frames just before it from the same camera and render them in the image's own
512x512 view with the affine M that prep_temporal.py already found (fw_day2/manifest_day2.json), so that
(prev2, prev1, current) line up without a new registration.  Negatives use the M of the earliest registered
image of their sequence, as prep_temporal.py did.  A residual shift (camera jitter) is estimated by phase
correlation against the current frame and applied when it raises the NCC.

Output (FW_OUT, default /kaggle/working/fw_motion), three files so the Output stays easy to download:
  manifest_motion.json   {items: {id: {split, p1, gap1, ncc1, shift1, p2, ...}}, settings}
  fw_motion_eval.zip     fw_motion/manifest_motion.json + fw_motion/prev/<id>__p{1,2}.jpg of val, test, negatives
  fw_motion_train.zip    the same for train (extract under /kaggle/temp before training)
gap = seconds before the image; ncc = normalised cross-correlation (grey) of the rendered frame with the image.

Local check:  FW_MANIFEST=<manifest_day2.json> FW_SEG_ROOT=<SmokeSeg> FW_OUT=<dir> FW_SEQS=<seq> FW_LIMIT=4
"""
import collections
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image

T0 = time.time()
KAGGLE = Path('/kaggle/input').is_dir()
TMP = Path('/kaggle/temp') if Path('/kaggle/temp').is_dir() else Path(os.environ.get('FW_TMP', '/tmp'))
OUT = Path(os.environ.get('FW_OUT', '/kaggle/working/fw_motion'))
OUT.mkdir(parents=True, exist_ok=True)
PREV = TMP / 'fw_motion_frames'   # scratch: the frames go into the two zips, not one file each into the Output
(PREV / 'prev').mkdir(parents=True, exist_ok=True)
BASES = ['https://cdn.hpwren.ucsd.edu/HPWREN-FIgLib-Data/']
SIZE = (512, 512)
MAX_GAP = {1: 150, 2: 300}   # seconds: p1 at most 2.5 min, p2 at most 5 min before the image
LIMIT = int(os.environ.get('FW_LIMIT', 0))
ONLY = set(filter(None, os.environ.get('FW_SEQS', '').split(',')))
WORKERS = int(os.environ.get('FW_WORKERS', 8))


def log(tag, **kw):
    print(tag, json.dumps(kw, default=str), flush=True)


MF = Path(os.environ['FW_MANIFEST']) if os.environ.get('FW_MANIFEST') else next(Path('/kaggle/input').rglob('fw_day2/manifest_day2.json'))
PREP = MF.parent
man = json.loads(MF.read_text())
if os.environ.get('FW_SEG_ROOT'):
    SEG = Path(os.environ['FW_SEG_ROOT'])
else:
    subprocess.run([sys.executable, '-m', 'pip', '-q', 'install', 'gdown'], check=True)
    import gdown
    archive = TMP / 'SmokeSeg.zip'
    if not archive.is_file():
        gdown.download(id='1AIHZ5PckjHGqeaG3VY5WZgH-Y-5lAzA0', output=str(archive), quiet=True)
    root = TMP / 'SmokeSeg-data'
    with zipfile.ZipFile(archive) as z:
        for n in z.namelist():
            assert (root / n).resolve().is_relative_to(root.resolve())
        z.extractall(root)
    SEG = next(p.parent for p in root.rglob('JPEGImages') if (p.parent / 'SegmentationClass').is_dir())

# ---------------------------------------------------------------- items: (id, seq, split, offset, current image, M)
ITEMS = collections.defaultdict(list)
first = {}
for split in ('train', 'val', 'test'):
    for r in man[split]:
        if r.get('M') is None:
            continue
        ITEMS[r['seq']].append(dict(id=r['id'], seq=r['seq'], split=split, offset=r['offset'], cur=SEG / r['image'], M=r['M']))
        if r['seq'] not in first or r['ts'] < first[r['seq']][0]:
            first[r['seq']] = (r['ts'], r['M'])
for n in man['negatives']:
    if n['split'] in ('val', 'test') and n['seq'] in first:
        ITEMS[n['seq']].append(dict(id=n['id'], seq=n['seq'], split='neg_' + n['split'], offset=n['offset'],
                                    cur=PREP / n['image'], M=first[n['seq']][1]))
if ONLY:
    ITEMS = {s: v for s, v in ITEMS.items() if s in ONLY}
log('ITEMS', sequences=len(ITEMS), per_split=collections.Counter(it['split'] for v in ITEMS.values() for it in v))

session = requests.Session()
session.headers['User-Agent'] = 'ForestWatch-research/1.0 (Kaggle notebook)'


def get(url, timeout=60, tries=3):
    for t in range(tries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (t + 1))
    return None


def listing(seq):
    for base in BASES:
        r = get(base + seq + '/index.html', timeout=30) or get(base + seq + '/', timeout=30)
        if r is not None:
            found = re.findall(r'(\d{10}_(?:\+|-|%2B|%2b)\d{5}\.jpg)', r.text)
            names = sorted({requests.utils.unquote(n) for n in found})
            if names:
                return base, names
    return None, []


def fetch_rgb(url):
    r = get(url)
    if r is None:
        return None
    try:
        return np.asarray(Image.open(io.BytesIO(r.content)).convert('RGB'), np.uint8)
    except Exception:
        return None


def q(name):
    return name.replace('+', '%2B')


def warp_to_seg(src_rgb, M):
    """As in prep_temporal.py: the 512x512 SmokeSeg view of a full-res frame (M maps view -> frame)."""
    M = np.asarray(M, np.float64)
    s = float(np.sqrt(abs(np.linalg.det(M[:, :2]))))
    k = max(1.0, s / 1.5)
    if k > 1.0:
        H, W = src_rgb.shape[:2]
        src_rgb = cv2.resize(src_rgb, (int(round(W / k)), int(round(H / k))), interpolation=cv2.INTER_AREA)
        M = M / k
    return cv2.warpAffine(src_rgb, M, SIZE, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)


def ncc(a, b):
    a, b = a.astype(np.float32), b.astype(np.float32)
    return float(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std() + 1e-6))


def refine(view, cur):
    """Apply the phase-correlation shift (camera jitter) when it raises the NCC with the current frame."""
    g1 = cv2.cvtColor(cur, cv2.COLOR_RGB2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(view, cv2.COLOR_RGB2GRAY).astype(np.float32)
    n0 = ncc(g2, g1)
    best = (n0, view, (0.0, 0.0))
    (dx, dy), _ = cv2.phaseCorrelate(g2, g1, cv2.createHanningWindow(g1.shape[::-1], cv2.CV_32F))
    if 0.3 <= float(np.hypot(dx, dy)) <= 12:
        for sgn in (1, -1):
            T = np.float32([[1, 0, sgn * dx], [0, 1, sgn * dy]])
            v = cv2.warpAffine(view, T, SIZE, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            n = ncc(cv2.cvtColor(v, cv2.COLOR_RGB2GRAY), g1)
            if n > best[0] + 0.002:
                best = (n, v, (round(sgn * dx, 2), round(sgn * dy, 2)))
    return best[1], round(n0, 4), round(best[0], 4), best[2]


def current(path):
    with Image.open(path) as im:
        return np.asarray(im.convert('RGB').resize(SIZE, Image.Resampling.BILINEAR), np.uint8)


def process(seq):
    t = time.time()
    base, names = listing(seq)
    res, out = dict(seq=seq, n_listed=len(names)), {}
    if not names:
        res['status'] = 'no_listing'
        return res, out
    by_off = {int(nm[:-4].split('_')[1]): nm for nm in names}
    offs = sorted(by_off)
    items = sorted(ITEMS[seq], key=lambda it: it['offset'])
    if LIMIT:
        items = items[:LIMIT]
    cache, shape = {}, None
    for it in items:
        o, rec = it['offset'], dict(split=it['split'])
        cur = current(it['cur'])
        for k, po in enumerate([x for x in offs if x < o][-2:][::-1], 1):
            if o - po > MAX_GAP[k]:
                continue
            if po not in cache:
                fr = fetch_rgb(base + seq + '/' + q(by_off[po]))
                if fr is None:
                    continue
                cache[po] = fr
            fr = cache[po]
            shape = shape or fr.shape
            if fr.shape != shape:
                continue
            view, n0, n1, sh = refine(warp_to_seg(fr, it['M']), cur)
            rel = f"prev/{it['id']}__p{k}.jpg"
            Image.fromarray(view).save(PREV / rel, quality=95)
            rec.update({f'p{k}': rel, f'gap{k}': o - po, f'ncc{k}_raw': n0, f'ncc{k}': n1, f'shift{k}': sh})
        out[it['id']] = rec
        for key in [x for x in cache if x < o - 360]:
            del cache[key]
    vals = [r['ncc1'] for r in out.values() if 'ncc1' in r]
    res.update(status='ok', n_items=len(items), n_p1=sum('p1' in r for r in out.values()),
               n_p2=sum('p2' in r for r in out.values()), ncc1_median=float(np.median(vals)) if vals else None,
               seconds=round(time.time() - t, 1))
    return res, out


def safe(seq):
    try:
        return process(seq)
    except Exception as e:  # one broken sequence must not stop the others
        return dict(seq=seq, status='error', error=repr(e)[:300]), {}


results, items_out = [], {}
seqs = sorted(ITEMS, key=lambda s: (not any(it['split'] != 'train' for it in ITEMS[s]), s))  # val/test cameras first
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    for i, (res, out) in enumerate(pool.map(safe, seqs)):
        results.append(res)
        items_out.update(out)
        if (i + 1) % 10 == 0 or res['status'] != 'ok':
            log('SEQ', i=i + 1, of=len(seqs), **res)

manifest = dict(items=items_out, sequences=results,
                settings=dict(max_gap=MAX_GAP, size=SIZE, source='FIgLib CDN ' + BASES[0],
                              note='previous frames rendered with the M of manifest_day2.json (negatives: earliest registered '
                                   'image of the sequence), then shifted by phase correlation when that raised the NCC'))
(OUT / 'manifest_motion.json').write_text(json.dumps(manifest))
for name, keep in (('fw_motion_eval.zip', lambda s: s != 'train'), ('fw_motion_train.zip', lambda s: s == 'train')):
    with zipfile.ZipFile(OUT / name, 'w', zipfile.ZIP_STORED) as z:  # JPEGs: storing is as small as deflating
        z.write(OUT / 'manifest_motion.json', 'fw_motion/manifest_motion.json')
        for iid, r in items_out.items():
            if keep(r['split']):
                for k in (1, 2):
                    if f'p{k}' in r:
                        z.write(PREV / r[f'p{k}'], 'fw_motion/' + r[f'p{k}'])


def stats(split):
    rs = [r for r in items_out.values() if r['split'] == split]
    n1 = [r['ncc1'] for r in rs if 'ncc1' in r]
    g1 = [r['gap1'] for r in rs if 'gap1' in r]
    g2 = [r['gap2'] for r in rs if 'gap2' in r]
    sh = [float(np.hypot(*r['shift1'])) for r in rs if 'shift1' in r]
    return dict(items=len(rs), with_p1=len(n1), with_p2=len(g2),
                gap1_median=float(np.median(g1)) if g1 else None, gap2_median=float(np.median(g2)) if g2 else None,
                ncc1_median=float(np.median(n1)) if n1 else None, ncc1_p05=float(np.percentile(n1, 5)) if n1 else None,
                shifted=sum(s > 0 for s in sh), shift_p95=float(np.percentile(sh, 95)) if sh else None)


log('MOTION_DONE', seconds=round(time.time() - T0), sequences=len(results),
    status=collections.Counter(r['status'] for r in results),
    splits={s: stats(s) for s in ('train', 'val', 'test', 'neg_val', 'neg_test')},
    zip_mb={n: round((OUT / n).stat().st_size / 1e6, 1) for n in ('fw_motion_eval.zip', 'fw_motion_train.zip')})
