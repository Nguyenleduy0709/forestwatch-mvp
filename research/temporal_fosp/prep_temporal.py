"""ForestWatch Day 2 (CPU): temporal references + true negatives for SmokeSeg.

For every SmokeSeg sequence (event_camera) this script
  1. rebuilds the Day-1 date-grouped split (same hash, same SMOKE5K exclusion),
  2. downloads pre-ignition FIgLib frames 20-40 min before ignition and stores their
     per-pixel median (resized exactly like SmokeSeg: full frame -> 512x512) as a
     smoke-free long-term reference,
  3. verifies that SmokeSeg images are full-frame resizes of FIgLib frames (PSNR),
  4. downloads pre-ignition frames 1-15 min before ignition of val/test sequences as
     true-negative frames for false-alarm measurement.
Everything is printed as JSON lines so results can be read from the Kaggle log.
"""
import collections, hashlib, io, json, re, subprocess, sys, time, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image

T0 = time.time()
WORK = Path('/kaggle/working')
OUT = WORK / 'fw_day2'
TMP = Path('/kaggle/temp') if Path('/kaggle/temp').is_dir() else Path('/tmp')
(OUT / 'refs').mkdir(parents=True, exist_ok=True)  # per-image aligned refs
(OUT / 'negatives').mkdir(parents=True, exist_ok=True)
# FIgLib moved to a CDN (checked 2026-11-06): https://www.hpwren.ucsd.edu/FIgLib/ links here.
BASES = ['https://cdn.hpwren.ucsd.edu/HPWREN-FIgLib-Data/']
REF_WINDOW = (-2400, -1200)     # seconds relative to ignition: 40..20 min before
NEG_WINDOW = (-900, -60)        # 15..1 min before ignition (val/test only)
N_REF, N_NEG = 7, 6
SIZE = (512, 512)


def log(tag, **kw):
    print(tag, json.dumps(kw, default=str), flush=True)


# ---------------------------------------------------------------- SmokeSeg ids
subprocess.run([sys.executable, '-m', 'pip', '-q', 'install', 'gdown'], check=True)
import gdown  # noqa: E402
archive = TMP / 'SmokeSeg.zip'
if not archive.is_file():
    gdown.download(id='1AIHZ5PckjHGqeaG3VY5WZgH-Y-5lAzA0', output=str(archive), quiet=True)
data_root = TMP / 'SmokeSeg-data'
with zipfile.ZipFile(archive) as z:
    for n in z.namelist():
        assert (data_root / n).resolve().is_relative_to(data_root.resolve())
    z.extractall(data_root)
seg_root = next(p.parent for p in data_root.rglob('JPEGImages') if (p.parent / 'SegmentationClass').is_dir())
ims = {p.stem: p for p in (seg_root / 'JPEGImages').iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png') and not p.name.startswith('.')}
masks = {p.stem: p for p in (seg_root / 'SegmentationClass').iterdir() if p.suffix == '.png' and not p.name.startswith('.')}

# Day-1 exclusion: dates whose frame ids were used by SMOKE5K pre-training.
seen = set()
for mf in Path('/kaggle/input').rglob('fosp_smoke5k_14000/manifest.json'):
    old = json.loads(mf.read_text())
    seen |= {x['id'] for k in ('train', 'val', 'test') for x in old.get(k, [])}
    break
rows, excluded = [], set()
for key, p in ims.items():
    if key not in masks or '__' not in key:
        continue
    seq, stem = key.rsplit('__', 1)
    m = re.fullmatch(r'(\d{10})_([+-]\d+)', stem)
    if not m:
        continue
    date = seq.split('_')[0]
    if stem in seen:
        excluded.add(date)
    cam = seq.split('_')[-1]
    rows.append(dict(id=key, seq=seq, event=date, camera=cam, station=cam.split('-')[0],
                     ts=int(m[1]), offset=int(m[2]),
                     image=str(p.relative_to(seg_root)), mask=str(masks[key].relative_to(seg_root))))
rows = [r for r in rows if r['event'] not in excluded]
for r in rows:
    h = int(hashlib.sha256(('42:' + r['event']).encode()).hexdigest()[:8], 16) % 10
    r['split'] = 'test' if h == 0 else 'val' if h == 1 else 'train'
# Day-1 pair flag: annotated frame with an annotated predecessor <= 300 s earlier.
by_seq = collections.defaultdict(list)
for r in rows:
    by_seq[r['seq']].append(r)
for items in by_seq.values():
    items.sort(key=lambda r: r['ts'])
    for a, b in zip(items, items[1:]):
        if 0 < b['ts'] - a['ts'] <= 300:
            b['day1_pair'] = True
log('SPLIT', n_rows=len(rows), excluded_dates=sorted(excluded), sequences=len(by_seq),
    per_split={s: dict(images=sum(r['split'] == s for r in rows),
                       events=len({r['event'] for r in rows if r['split'] == s}),
                       stations=len({r['station'] for r in rows if r['split'] == s}),
                       day1_pairs=sum(r['split'] == s and r.get('day1_pair', False) for r in rows))
               for s in ('train', 'val', 'test')},
    station_overlap_train_test=len({r['station'] for r in rows if r['split'] == 'train'} & {r['station'] for r in rows if r['split'] == 'test'}),
    offsets_min_max=[min(r['offset'] for r in rows), max(r['offset'] for r in rows)])

# ---------------------------------------------------------------- FIgLib access
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
        return Image.open(io.BytesIO(r.content)).convert('RGB')
    except Exception:
        return None


def psnr(a, b):
    mse = np.mean((np.asarray(a, np.float32) - np.asarray(b, np.float32)) ** 2)
    return float(10 * np.log10(255.0 ** 2 / max(mse, 1e-6)))


def q(name):
    """S3/CDN paths treat a literal '+' as a space."""
    return name.replace('+', '%2B')


def register(seg_rgb, src_rgb):
    """Affine M (2x3) mapping SmokeSeg pixel coords -> full-res FIgLib coords, plus quality."""
    import cv2
    H, W = src_rgb.shape[:2]
    f = 1024.0 / max(H, W)
    small = cv2.resize(src_rgb, (int(round(W * f)), int(round(H * f))), interpolation=cv2.INTER_AREA)
    g1 = cv2.cvtColor(seg_rgb, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    sift = cv2.SIFT_create(nfeatures=4000)
    k1, d1 = sift.detectAndCompute(g1, None)
    k2, d2 = sift.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return None, dict(inliers=0)
    good = [m for m, n in (x for x in cv2.BFMatcher().knnMatch(d1, d2, k=2) if len(x) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 8:
        return None, dict(inliers=len(good))
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffine2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=4000)
    if M is None:
        return None, dict(inliers=0)
    M = M.astype(np.float64)
    M[:, :] /= f                       # small-image coords -> full-res coords
    warped = warp_to_seg(src_rgb, M)
    a = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY).astype(np.float32)
    b = g1.astype(np.float32)
    ncc = float(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std() + 1e-6))
    return M, dict(inliers=int(inl.sum()), ncc=round(ncc, 4), psnr=round(psnr(warped, seg_rgb), 2),
                   scale=round(float(np.sqrt(abs(np.linalg.det(M[:, :2])))), 3))


def warp_to_seg(src_rgb, M):
    """Render the 512x512 SmokeSeg view of a full-res frame given M (seg -> src), anti-aliased."""
    import cv2
    s = float(np.sqrt(abs(np.linalg.det(M[:, :2]))))
    k = max(1.0, s / 1.5)
    if k > 1.0:
        H, W = src_rgb.shape[:2]
        src_rgb = cv2.resize(src_rgb, (int(round(W / k)), int(round(H / k))), interpolation=cv2.INTER_AREA)
        M = M / k
    return cv2.warpAffine(src_rgb, M, SIZE, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)


def pick(names, lo, hi, n):
    cands = [(int(nm.split('_')[1][:-4]), nm) for nm in names]
    cands = sorted(nm for off, nm in cands if lo <= off <= hi)
    if len(cands) <= n:
        return cands
    idx = np.linspace(0, len(cands) - 1, n).round().astype(int)
    return [cands[i] for i in idx]


split_of_seq = {seq: items[0]['split'] for seq, items in by_seq.items()}


def process(seq):
    """Per-image registration of SmokeSeg crops to FIgLib frames + aligned smoke-free references."""
    t = time.time()
    base, names = listing(seq)
    res = dict(seq=seq, split=split_of_seq[seq], n_listed=len(names), base=base)
    updates, negs = {}, []
    if not names:
        res['status'] = 'no_listing'
        return res, updates, negs
    frames = []
    for nm in pick(names, REF_WINDOW[0], REF_WINDOW[1], N_REF):
        im = fetch_rgb(base + seq + '/' + q(nm))
        if im is not None:
            frames.append(np.asarray(im, np.uint8))
    shapes = {f.shape for f in frames}
    res['n_ref_frames'] = len(frames)
    if len(frames) < 3 or len(shapes) != 1:
        res['status'] = 'no_ref'
        return res, updates, negs
    ref_full = np.median(np.stack(frames), axis=0).astype(np.uint8)
    del frames
    first_M, nccs = None, []
    for r in by_seq[seq]:
        stem = r['id'].split('__')[1]
        src = fetch_rgb(base + seq + '/' + q(stem) + '.jpg')
        info = dict(ok=False)
        if src is not None:
            src = np.asarray(src, np.uint8)
            with Image.open(seg_root / r['image']) as im:
                seg_rgb = np.asarray(im.convert('RGB').resize(SIZE, Image.Resampling.BILINEAR), np.uint8)
            if src.shape == ref_full.shape:
                M, info = register(seg_rgb, src)
                info['ok'] = bool(M is not None and info.get('ncc', 0) >= 0.90)
                if info['ok']:
                    rel = 'refs/' + r['id'] + '.jpg'
                    Image.fromarray(warp_to_seg(ref_full, M)).save(OUT / rel, quality=95)
                    updates[r['id']] = dict(ref=rel, reg=info, M=M.round(5).tolist())
                    nccs.append(info['ncc'])
                    if first_M is None:
                        first_M = M
            else:
                info['shape_mismatch'] = True
        if r['id'] not in updates:
            updates[r['id']] = dict(ref=None, reg=info)
    res['n_registered'] = len(nccs)
    res['n_rows'] = len(by_seq[seq])
    res['ncc_median'] = float(np.median(nccs)) if nccs else None
    if first_M is not None and res['split'] in ('val', 'test'):
        for nm in pick(names, NEG_WINDOW[0], NEG_WINDOW[1], N_NEG):
            im = fetch_rgb(base + seq + '/' + q(nm))
            if im is None or np.asarray(im).shape != ref_full.shape:
                continue
            nid = seq + '__' + nm[:-4]
            img_rel, ref_rel = 'negatives/' + nid + '.jpg', 'negatives/' + nid + '__ref.jpg'
            Image.fromarray(warp_to_seg(np.asarray(im, np.uint8), first_M)).save(OUT / img_rel, quality=95)
            Image.fromarray(warp_to_seg(ref_full, first_M)).save(OUT / ref_rel, quality=95)
            ts, off = nm[:-4].split('_')
            negs.append(dict(id=nid, seq=seq, split=res['split'], ts=int(ts), offset=int(off), image=img_rel, ref=ref_rel))
    res['n_negatives'] = len(negs)
    res['status'] = 'ok' if nccs else 'no_registration'
    res['seconds'] = round(time.time() - t, 1)
    return res, updates, negs


seqs = sorted(by_seq, key=lambda s: (split_of_seq[s] != 'test', split_of_seq[s] != 'val', s))
results, negatives, row_updates = [], [], {}
with ThreadPoolExecutor(max_workers=8) as pool:
    for i, (res, upd, negs) in enumerate(pool.map(process, seqs)):
        results.append(res)
        negatives += negs
        row_updates.update(upd)
        if (i + 1) % 10 == 0 or res['status'] != 'ok':
            log('SEQ', i=i + 1, of=len(seqs), **{k: res.get(k) for k in ('seq', 'split', 'status', 'n_listed', 'n_ref_frames', 'n_rows', 'n_registered', 'ncc_median', 'n_negatives', 'seconds')})

for r in rows:
    r.update(row_updates.get(r['id'], dict(ref=None, reg=dict(ok=False))))
manifest = {s: [r for r in rows if r['split'] == s] for s in ('train', 'val', 'test')}
manifest['negatives'] = negatives
manifest['sequences'] = results
manifest['settings'] = dict(ref_window=REF_WINDOW, neg_window=NEG_WINDOW, n_ref=N_REF, n_neg=N_NEG, size=SIZE,
                            note='per-image refs: pre-ignition median (20-40 min before ignition) warped with the SIFT/RANSAC affine '
                                 'that maps each SmokeSeg crop onto its own FIgLib source frame (accepted if NCC >= 0.90)')
(OUT / 'manifest_day2.json').write_text(json.dumps(manifest, indent=1))
nccs = [r['reg']['ncc'] for r in rows if r.get('reg', {}).get('ok')]
scales = [r['reg']['scale'] for r in rows if r.get('reg', {}).get('ok')]
log('PREP_DONE', seconds=round(time.time() - T0), sequences=len(results),
    status=collections.Counter(r['status'] for r in results),
    rows_with_ref={s: f"{sum(1 for r in manifest[s] if r['ref'])}/{len(manifest[s])}" for s in ('train', 'val', 'test')},
    negatives={s: sum(n['split'] == s for n in negatives) for s in ('val', 'test')},
    registration=dict(n_ok=len(nccs), ncc_median=float(np.median(nccs)) if nccs else None,
                      ncc_p05=float(np.percentile(nccs, 5)) if nccs else None,
                      scale_median=float(np.median(scales)) if scales else None,
                      scale_p05_p95=[float(np.percentile(scales, 5)), float(np.percentile(scales, 95))] if scales else None))
