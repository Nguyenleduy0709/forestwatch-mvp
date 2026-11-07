"""ForestWatch Day 2 (GPU pilot): FoSp vs FoSp-Temporal on the Day-1 date-grouped SmokeSeg split.

E0  FoSp trained on SMOKE5K (14k updates), frozen          -> transfer baseline
E1  E0 fine-tuned on SmokeSeg-train, single frame           -> strong single-frame baseline
E2  E0 fine-tuned with the same budget + observed background + learned arbitration
All three use the same code path, fixed MAT latent (z = 0), threshold 0.4, 512x512 masks.
Every result is printed as one JSON line (tag + payload) so it can be read from the log.
"""
import collections
import json
import os
import random
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageEnhance

T0 = time.time()
ROOT = Path(os.environ.get('FW_KAGGLE_ROOT', '/kaggle'))
DRY = os.environ.get('FW_DRYRUN') == '1'   # CPU smoke test of the whole pipeline (no GPU, tiny data)
if DRY:
    torch.Tensor.cuda = lambda self, *a, **k: self
    torch.nn.Module.cuda = lambda self, *a, **k: self
    _orig_to = torch.nn.Module.to
    torch.nn.Module.to = lambda self, *a, **k: self if (a and isinstance(a[0], torch.device) and a[0].type == 'cuda') else _orig_to(self, *a, **k)
    torch.cuda.get_device_name = lambda *a: 'cpu-dryrun'
    torch.cuda.max_memory_allocated = lambda *a: 0
    torch.cuda.empty_cache = lambda: None
TOTAL_BUDGET = float(os.environ.get('FW_TOTAL_BUDGET', 2 * 3600 + 5 * 60))
E1_TRAIN_BUDGET = float(os.environ.get('FW_E1_BUDGET', 35 * 60))
CFG = dict(micro_batch=2, accumulate=3, base_lr=6e-5, warmup=100, max_updates=3000, threshold=0.4,
           rel_weight=0.5, seed=42, n_val_evals=2, init_bias=2.5)
CFG['max_updates'] = int(os.environ.get('FW_MAX_UPDATES', CFG['max_updates']))
# Local / continued runs (unset -> identical to the Kaggle pilot).
for _key, _env in (('micro_batch', 'FW_MICRO_BATCH'), ('accumulate', 'FW_ACCUM'), ('n_val_evals', 'FW_N_VAL_EVALS')):
    if os.environ.get(_env):
        CFG[_key] = int(os.environ[_env])
CFG['fusion'] = os.environ.get('FW_FUSION', 'convex')          # 'residual' = zero-init additive observed branch
if os.environ.get('FW_INIT_BIAS'):
    CFG['init_bias'] = float(os.environ['FW_INIT_BIAS'])        # arbitration prior towards FoSp's own background
# Share of training samples replaced by a smoke-free scene: the image's own pre-ignition reference (empty mask),
# paired with the reference of another image of the same sequence.  Same share for E1 and E2.
CFG['train_neg'] = float(os.environ.get('FW_TRAIN_NEG', 0))
# Gate supervision: 'one_sided' (pilot: penalise weight on known-invalid pixels only) or 'bce' (also pull it up on valid ones).
CFG['gate_loss'] = os.environ.get('FW_GATE_LOSS', 'one_sided')
assert CFG['gate_loss'] in ('one_sided', 'bce'), CFG['gate_loss']
# SMOKE5K (the data FoSp-14k was trained on): FW_S5K_TRAIN=1 adds its 4589 training pairs to the training rows
# (no reference frame, so E2 sees them as no-history samples); FW_S5K_EVAL=1 also scores every model on the
# official 400-image SMOKE5K test at native mask resolution (FoSp's own protocol).  Checkpoint selection stays
# SmokeSeg val image-F1.  No SMOKE5K recording overlaps our SmokeSeg train/val/test (checked on the manifests).
CFG['s5k_train'] = os.environ.get('FW_S5K_TRAIN') == '1'
S5K_EVAL = os.environ.get('FW_S5K_EVAL') == '1' or CFG['s5k_train']
WORKERS = int(os.environ.get('FW_WORKERS', 0 if DRY else 3))   # use 0 on Windows (spawn re-runs this script)
EVAL_BATCH = int(os.environ.get('FW_EVAL_BATCH', 4))
FIXED_UPDATES = int(os.environ.get('FW_UPDATES', 0)) or None    # same update count for E1 and E2, no E1 time budget
RUNS = [r.strip() for r in os.environ.get('FW_RUNS', 'E0,E1,E2').split(',')]
INIT = {'E1': os.environ.get('FW_INIT_E1'), 'E2': os.environ.get('FW_INIT_E2')}  # warm start from *_best.pth
NO_PIP = os.environ.get('FW_NO_PIP') == '1'
assert 'E1' in RUNS or FIXED_UPDATES or 'E2' not in RUNS, 'E2 without E1 needs FW_UPDATES'
# Laptop protection: pause (never kill) so E1 and E2 still get the same number of updates.
THERMAL_MAX = float(os.environ.get('FW_THERMAL_MAX', 0))        # pause when GPU temp >= this twice in a row (~1 min); 0 = off
THERMAL_RESUME = float(os.environ.get('FW_THERMAL_RESUME', 75))  # resume once the GPU has cooled to this
REST_EVERY = float(os.environ.get('FW_REST_EVERY', 0))          # seconds of training between rests; 0 = off
REST_SECONDS = float(os.environ.get('FW_REST_SECONDS', 600))
FW_SRC = r'''__FW_TEMPORAL_SOURCE__'''
PAUSED = [0.0]  # seconds spent in thermal pauses / rests, excluded from the time budget


def log(tag, **kw):
    print(tag, json.dumps(kw, default=str), flush=True)


def elapsed():
    return time.time() - T0 - PAUSED[0]


def gpu_status():
    """(temperature °C or None, [power W, SM clock MHz, utilisation %]) from nvidia-smi."""
    try:
        line = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu,power.draw,clocks.sm,utilization.gpu',
                               '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        vals = [v.strip() for v in line.split(',')]
        return float(vals[0]), vals[1:]
    except Exception:  # noqa: BLE001 - monitoring must never break training
        return None, []


class ThermalGuard:
    def __init__(self):
        self.last_check, self.hot, self.since_rest = 0.0, 0, time.time()

    def _sleep(self, seconds):
        time.sleep(seconds)
        PAUSED[0] += seconds

    def step(self, run, update):
        if REST_EVERY and time.time() - self.since_rest >= REST_EVERY:
            log('REST', run=run, update=update, seconds=REST_SECONDS, temp=gpu_status()[0])
            self._sleep(REST_SECONDS)
            self.since_rest = time.time()
        if not THERMAL_MAX or time.time() - self.last_check < 30:
            return
        self.last_check = time.time()
        temp, rest = gpu_status()
        with open(OUT / 'thermal.csv', 'a', encoding='utf-8') as f:
            f.write(','.join([str(round(elapsed())), run, str(update), str(temp)] + rest) + '\n')
        self.hot = self.hot + 1 if temp is not None and temp >= THERMAL_MAX else 0
        if self.hot < 2:
            return
        log('THERMAL_PAUSE', run=run, update=update, temp=temp)
        started = time.time()
        while temp is not None and temp > THERMAL_RESUME and time.time() - started < 1800:
            self._sleep(30)
            temp, _ = gpu_status()
        log('THERMAL_RESUME', run=run, update=update, temp=temp, paused_s=round(time.time() - started))
        self.hot, self.last_check = 0, time.time()


GUARD = ThermalGuard()


random.seed(CFG['seed']); np.random.seed(CFG['seed']); torch.manual_seed(CFG['seed'])
assert torch.cuda.is_available() or DRY, 'enable GPU'
torch.backends.cudnn.benchmark = True
TMP = ROOT / 'temp'; TMP.mkdir(exist_ok=True)
OUT = Path(os.environ['FW_OUT']) if os.environ.get('FW_OUT') else ROOT / 'working' / 'fw_day2_run'
OUT.mkdir(parents=True, exist_ok=True)
(TMP / 'fw_temporal.py').write_text(FW_SRC)
sys.path.insert(0, str(TMP))
import fw_temporal as fwt  # noqa: E402

# ------------------------------------------------------------------ FoSp environment (as in the user's notebooks)
INP = ROOT / 'input'
if os.environ.get('FW_FOSP_REPO'):
    repo = Path(os.environ['FW_FOSP_REPO'])
else:
    repo = next(INP.rglob('FoSp-official/config/fosp_config.py')).parent.parent
snapshot = repo.parent
mmcv_dir = Path(os.environ['FW_MMCV_DIR']) if os.environ.get('FW_MMCV_DIR') else snapshot / 'mmcv-1.6.2-source'
if not (mmcv_dir / 'mmcv').is_dir():
    mmcv_dir = TMP / 'mmcv'
    if not (mmcv_dir / 'mmcv').is_dir():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'v1.6.2', 'https://github.com/open-mmlab/mmcv.git', str(mmcv_dir)], check=True)
deps = Path(os.environ['FW_DEPS_DIR']) if os.environ.get('FW_DEPS_DIR') else next((p.parent.parent for p in INP.rglob('fosp-deps/timm/__init__.py')), None)
if deps is None:
    deps = TMP / 'fosp-deps'
    if not NO_PIP:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--target', str(deps), '--no-deps', 'setuptools==80.9.0', 'wheel',
                        'addict', 'yapf==0.40.1', 'easydict', 'future', 'ninja', 'timm', 'gdown', 'platformdirs', 'tomli'], check=True)
if not DRY and not NO_PIP:
    subprocess.run([sys.executable, '-m', 'pip', '-q', 'install', 'addict', 'yapf==0.40.1', 'easydict', 'timm==0.6.13', 'regex',
                    'setuptools<81', 'gdown'], check=True)
sys.path[:0] = [str(repo), str(repo / 'inpainter_setting'), str(mmcv_dir), str(deps)]
os.environ['PYTHONPATH'] = os.pathsep.join(sys.path)
import logging  # noqa: E402
from mmcv import Config  # noqa: E402
from mmseg.models import build_segmentor  # noqa: E402
from inpainter_setting.torch_utils.ops import bias_act, upfirdn2d  # noqa: E402
from torch_utils.ops import bias_act as ba, upfirdn2d as uf  # noqa: E402
for op in (bias_act, upfirdn2d, ba, uf):
    op._init = lambda: False
logging.getLogger('mmcv').setLevel(logging.WARNING)
cfg = Config.fromfile(str(repo / 'config/fosp_config.py'))
cfg.model.pretrained = None
CKPT = Path(os.environ['FW_CKPT']) if os.environ.get('FW_CKPT') else next(INP.rglob('fosp_smoke5k_14000/best.pth'))
log('ENV', repo=str(repo), mmcv=str(mmcv_dir), deps=str(deps), ckpt=str(CKPT), gpu=torch.cuda.get_device_name(0),
    torch=torch.__version__, seconds=round(elapsed()))


def build(use_temporal, init=None):
    seg = build_segmentor(cfg.model).cuda()
    for op in (bias_act, upfirdn2d, ba, uf):
        op._init = lambda: False
    state = torch.load(CKPT, map_location='cpu', weights_only=True)
    seg.load_state_dict(state['state_dict'], strict=True)
    del state
    model = fwt.TemporalFoSp(seg, num_refs=1, use_temporal=use_temporal, init_bias=CFG['init_bias'],
                             fusion=CFG['fusion'] if use_temporal else 'convex').cuda()
    if init:  # warm start from an earlier run of the same variant (*_best.pth written by train())
        if not Path(init).is_file():  # a pattern under the inputs, e.g. 'day3_s5k_b/E1_single_best.pth'
            hits = sorted(q for q in INP.rglob(init) if q.is_file())
            assert len(hits) == 1, f'FW_INIT {init!r}: {len(hits)} matches under {INP}: {hits[:3]}'
            init = hits[0]
        state = torch.load(init, map_location='cpu', weights_only=True)
        assert bool(state.get('use_temporal')) == use_temporal, f'{init}: use_temporal mismatch'
        model.load_state_dict(state['state_dict'], strict=True)
        log('INIT', path=str(init), use_temporal=use_temporal, history=state.get('history'))
        del state
    return model


# ------------------------------------------------------------------ data
if os.environ.get('FW_SEG_ROOT'):  # folder that holds JPEGImages/ and SegmentationClass/
    SEG = Path(os.environ['FW_SEG_ROOT'])
else:
    archive = TMP / 'SmokeSeg.zip'
    if not archive.is_file():
        import gdown
        gdown.download(id='1AIHZ5PckjHGqeaG3VY5WZgH-Y-5lAzA0', output=str(archive), quiet=True)
    data_root = TMP / 'SmokeSeg-data'
    if not data_root.is_dir():
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                assert (data_root / n).resolve().is_relative_to(data_root.resolve())
            z.extractall(data_root)
    SEG = next(p.parent for p in data_root.rglob('JPEGImages') if (p.parent / 'SegmentationClass').is_dir())
MF = Path(os.environ['FW_MANIFEST']) if os.environ.get('FW_MANIFEST') else next(INP.rglob('fw_day2/manifest_day2.json'))
PREP = MF.parent
manifest = json.loads(MF.read_text())
train_rows, val_rows, test_rows = manifest['train'], manifest['val'], manifest['test']
neg_rows = [n for n in manifest['negatives'] if n['split'] in ('val', 'test')]
val_neg_rows = [n for n in neg_rows if n['split'] == 'val']
if os.environ.get('FW_LIMIT'):  # smoke test only: first N rows of every split
    _n = int(os.environ['FW_LIMIT'])
    train_rows, val_rows, test_rows, neg_rows = train_rows[:_n], val_rows[:_n], test_rows[:_n], neg_rows[:_n]
    val_neg_rows = val_neg_rows[:_n]
for name, rows in (('train', train_rows), ('val', val_rows), ('test', test_rows)):
    log('DATA', split=name, images=len(rows), with_ref=sum(bool(r.get('ref')) for r in rows),
        events=len({r['event'] for r in rows}), day1_pairs=sum(bool(r.get('day1_pair')) for r in rows))
log('DATA', split='negatives', images=len(neg_rows), with_ref=sum(bool(n.get('ref')) for n in neg_rows), seconds=round(elapsed()))


def _find_s5k_root():
    for base in (INP, TMP):
        for p in base.rglob('img'):
            if p.parent.name == 'train' and (p.parent.parent / 'test' / 'gt_').is_dir() and p.parent.parent.name == 'SMOKE5K':
                return p.parent.parent
    return None


S5K_TRAIN, S5K_TEST = [], []
if CFG['s5k_train'] or S5K_EVAL:
    # Same rows as FoSp-14k: its manifest (train = 4589 pairs after its own validation holdout, test = 400).
    _s5k = json.loads((CKPT.parent / 'manifest.json').read_text())
    S5K_ROOT = Path(os.environ['FW_S5K_ROOT']) if os.environ.get('FW_S5K_ROOT') else _find_s5k_root()
    if S5K_ROOT is None:  # not attached as an input: the authors' archive, as in the FoSp-SMOKE5K notebook
        import gdown
        _zip = TMP / 'SMOKE5K.zip'
        if not _zip.is_file():
            gdown.download(id=_s5k['report']['source_archive_id'], output=str(_zip), quiet=True)
        with zipfile.ZipFile(_zip) as z:
            for n in z.namelist():
                assert (TMP / 'SMOKE5K-official' / n).resolve().is_relative_to((TMP / 'SMOKE5K-official').resolve())
            z.extractall(TMP / 'SMOKE5K-official')
        S5K_ROOT = _find_s5k_root()
    assert S5K_ROOT is not None, 'SMOKE5K not found'

    def _s5k_rows(split):
        rows = []
        for r in _s5k[split]:
            rel = lambda p: S5K_ROOT / p.replace('\\', '/').split('/SMOKE5K/', 1)[1]  # noqa: E731
            rows.append(dict(id='s5k:' + r['id'], src='s5k', image=str(rel(r['image'])), mask=str(rel(r['mask'])),
                             event=r['group'], seq='s5k:' + r['group'], offset=-1, day1_pair=False,
                             cam=re.fullmatch(r'\d{10}_[+-]\d{5}', r['id']) is not None))  # fixed-camera frame
        return rows
    S5K_TRAIN = _s5k_rows('train') if CFG['s5k_train'] else []
    S5K_TEST = _s5k_rows('test') if S5K_EVAL else []
    if os.environ.get('FW_LIMIT'):
        S5K_TRAIN, S5K_TEST = S5K_TRAIN[:int(os.environ['FW_LIMIT'])], S5K_TEST[:int(os.environ['FW_LIMIT'])]
    _missing = [r['id'] for r in S5K_TRAIN + S5K_TEST if not (Path(r['image']).is_file() and Path(r['mask']).is_file())]
    assert not _missing, f'SMOKE5K files missing: {len(_missing)}, e.g. {_missing[:3]}'
    log('DATA', split='smoke5k', root=str(S5K_ROOT), train=len(S5K_TRAIN), test=len(S5K_TEST),
        test_camera=sum(r['cam'] for r in S5K_TEST), seconds=round(elapsed()))

MEAN, STD = fwt.MEAN, fwt.STD
BIL, NEAR = Image.Resampling.BILINEAR, Image.Resampling.NEAREST


def mask_array(path):
    with Image.open(path) as m:
        return np.asarray(m.resize((512, 512), NEAR)).astype(np.int64)


donors = []
for r in train_rows:
    m = mask_array(SEG / r['mask'])
    if (m == 1).mean() >= 0.003:
        donors.append(r)
log('DONORS', n=len(donors), seconds=round(elapsed()))


def _crop_shift(ma, mb, size=512):
    """Largest displacement (crop pixels) between two crop->frame affines over the crop corners."""
    a, b = np.asarray(ma, np.float64), np.asarray(mb, np.float64)
    corners = np.array([[0, 0, 1], [size, 0, 1], [0, size, 1], [size, size, 1]], np.float64)
    scale = abs(np.linalg.det(a[:, :2])) ** 0.5
    return float(np.abs(corners @ (a - b).T).max() / scale)


# Training negatives: partner = another image of the same sequence whose reference is aligned with this
# one to <= 1.5 px, so (current, reference) is a smoke-free pair with only registration noise.
NEG_PARTNER = []
if CFG['train_neg'] > 0:
    _by_seq = collections.defaultdict(list)
    for _i, _r in enumerate(train_rows):
        if _r.get('ref') and _r.get('M') is not None:
            _by_seq[_r['seq']].append(_i)
    for _i, _r in enumerate(train_rows):
        same = [j for j in _by_seq.get(_r['seq'], []) if j != _i and _crop_shift(_r['M'], train_rows[j]['M']) <= 1.5]
        NEG_PARTNER.append(same)
    log('TRAIN_NEG', share=CFG['train_neg'], rows_with_ref=sum(bool(r.get('ref')) for r in train_rows),
        with_partner=sum(bool(p) for p in NEG_PARTNER), seconds=round(elapsed()))


class SmokeSet(torch.utils.data.Dataset):
    def __init__(self, rows, train=False, corrupt=False, negatives=False, swap_ref=None, neg_share=0.0):
        self.rows, self.train, self.corrupt, self.negatives = rows, train, corrupt, negatives
        self.swap_ref = swap_ref  # {seq: ref of a *different* camera} for the wrong-reference test
        self.neg_share = neg_share if train else 0.0  # training negatives, see NEG_PARTNER

    def __len__(self):
        return len(self.rows)

    def _img(self, path):
        with Image.open(path) as im:
            return im.convert('RGB').resize((512, 512), BIL)

    def __getitem__(self, i):
        r = self.rows[i]
        if self.neg_share and r.get('ref') and random.random() < self.neg_share:
            # smoke-free scene: this image's own pre-ignition reference, empty mask
            img = self._img(PREP / r['ref'])
            mask = Image.fromarray(np.zeros((512, 512), np.uint8))
            partners = NEG_PARTNER[i] if i < len(NEG_PARTNER) else []
            seq_ref = self.rows[random.choice(partners)]['ref'] if partners else r['ref']
        elif r.get('src') == 's5k':  # SMOKE5K pair: white (255) = smoke, no ignore label, no history
            img = self._img(r['image'])
            with Image.open(r['mask']) as m:
                mask = Image.fromarray((np.asarray(m.convert('L').resize((512, 512), NEAR)) > 127).astype(np.uint8))
            seq_ref = None
        else:
            img = self._img((PREP if self.negatives else SEG) / r['image'])
            mask = Image.fromarray(np.zeros((512, 512), np.uint8)) if self.negatives else Image.fromarray(mask_array(SEG / r['mask']).astype(np.uint8))
            seq_ref = r.get('ref')  # per-image registered reference (None -> no usable history)
        if self.swap_ref is not None:
            seq_ref = self.swap_ref.get(r['seq'])
        has = seq_ref is not None
        ref = self._img(PREP / seq_ref) if has else img.copy()
        if self.train:
            if random.random() < 0.5:
                img, mask, ref = (t.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for t in (img, mask, ref))
            side = random.randint(410, 512)
            left, top = random.randint(0, 512 - side), random.randint(0, 512 - side)
            box = (left, top, left + side, top + side)
            img, ref = (t.crop(box).resize((512, 512), BIL) for t in (img, ref))
            mask = mask.crop(box).resize((512, 512), NEAR)
            if random.random() < 0.5:
                img = ImageEnhance.Brightness(img).enhance(random.uniform(0.85, 1.15))
                img = ImageEnhance.Contrast(img).enhance(random.uniform(0.85, 1.15))
        a_img, a_ref, a_mask = np.asarray(img, np.uint8), np.asarray(ref, np.uint8), np.asarray(mask).astype(np.int64)
        valid = np.ones((512, 512), np.uint8)
        if self.train and self.corrupt and has:
            rng = np.random.default_rng(random.getrandbits(32))
            donor = None
            if donors and random.random() < 0.5:
                d = random.choice(donors)
                donor = (np.asarray(self._img(SEG / d['image']), np.uint8), (mask_array(SEG / d['mask']) == 1).astype(np.uint8))
            a_ref, valid, _ = fwt.corrupt_reference(a_img, a_ref, (a_mask == 1), donor, rng)
        x = torch.from_numpy(((a_img.astype(np.float32) - MEAN) / STD).transpose(2, 0, 1).copy())
        xr = torch.from_numpy(((a_ref.astype(np.float32) - MEAN) / STD).transpose(2, 0, 1).copy())
        return x, xr[None], torch.tensor(float(has)), torch.from_numpy(valid)[None], torch.from_numpy(a_mask)[None], i


REF_OF_SEQ = {}
for _r in train_rows + val_rows + test_rows:
    if _r.get('ref'):
        REF_OF_SEQ.setdefault(_r['seq'], _r['ref'])


def worker_init(wid):
    seed = (torch.initial_seed() + wid) % 2 ** 32
    random.seed(seed); np.random.seed(seed % 2 ** 32)


def loader(ds, batch, shuffle):
    return torch.utils.data.DataLoader(ds, batch_size=batch, shuffle=shuffle, num_workers=WORKERS, drop_last=shuffle,
                                       pin_memory=not DRY, persistent_workers=shuffle and WORKERS > 0, worker_init_fn=worker_init)


@torch.no_grad()
def evaluate_s5k(model, rows, use_temporal, label):
    """SMOKE5K test as in FoSp-SMOKE5K: 512 input, logits resized to the native mask, mask > 127 = smoke."""
    model.eval()
    out = []
    for r in rows:
        with Image.open(r['image']) as im:
            a = np.asarray(im.convert('RGB').resize((512, 512), BIL), np.float32)
        with Image.open(r['mask']) as m:
            gt = np.asarray(m.convert('L')) > 127
        x = torch.from_numpy(((a - MEAN) / STD).transpose(2, 0, 1).copy())[None].cuda()
        has = torch.zeros(1).cuda()  # no history: E2 falls back to the FoSp path exactly
        logits, _, _ = model(x, x[:, None] if use_temporal else None, has)
        prob = torch.sigmoid(F.interpolate(logits, size=gt.shape, mode='bilinear', align_corners=False))[0, 0].cpu().numpy()
        s = fwt.image_scores(prob > CFG['threshold'], gt)
        s.update(id=r['id'], event=r['event'], cam=r['cam'])
        out.append(s)
    return out


def report_s5k(tag, rows):
    payload = dict(all=fwt.summarize(rows), camera=fwt.summarize([r for r in rows if r['cam']]),
                   other=fwt.summarize([r for r in rows if not r['cam']]))
    log(tag, **payload)
    return payload


@torch.no_grad()
def evaluate(model, rows, use_temporal, label, negatives=False, no_ref=False, swap_ref=None):
    model.eval()
    out = []
    for x, xr, has, valid, y, idx in loader(SmokeSet(rows, negatives=negatives, swap_ref=swap_ref), EVAL_BATCH, False):
        if no_ref:
            has = torch.zeros_like(has)
        logits, _, w = model(x.cuda(non_blocking=True), xr.cuda(non_blocking=True) if use_temporal else None, has.cuda())
        prob = torch.sigmoid(F.interpolate(logits, size=(512, 512), mode='bilinear', align_corners=False))[:, 0].cpu().numpy()
        wobs = [F.interpolate(w[-1][:, 1:2], size=(512, 512), mode='bilinear', align_corners=False)[:, 0].cpu().numpy()] if w else None
        for j in range(len(idx)):
            r = rows[int(idx[j])]
            pred = prob[j] > CFG['threshold']
            if negatives:
                npx = int(pred.sum())
                s = dict(id=r['id'], event=r['seq'].split('_')[0], area=npx / pred.size, fa_any=npx > 0, fa_50=npx >= 50, fa_200=npx >= 200)
            else:
                gt = y[j, 0].numpy()
                s = fwt.image_scores(pred, gt == 1, gt == 255)
                s.update(id=r['id'], event=r['event'], offset=r['offset'], day1_pair=bool(r.get('day1_pair')),
                         has_ref=bool(has[j]), size=fwt.size_bucket(s['gt_frac']))
                if wobs is not None:
                    g = gt == 1
                    s['w_obs_smoke'] = float(wobs[0][j][g].mean()) if g.any() else None
                    s['w_obs_bg'] = float(wobs[0][j][~g].mean())
            out.append(s)
    return out


def report(tag, rows, negatives=False):
    if negatives:
        payload = dict(n=len(rows), fa_any=float(np.mean([r['fa_any'] for r in rows])), fa_50=float(np.mean([r['fa_50'] for r in rows])),
                       fa_200=float(np.mean([r['fa_200'] for r in rows])), mean_area=float(np.mean([r['area'] for r in rows])))
    else:
        payload = dict(all=fwt.summarize(rows),
                       size={b: fwt.summarize([r for r in rows if r['size'] == b]) for b in ('small', 'medium', 'large')},
                       offset={k: fwt.summarize([r for r in rows if lo <= r['offset'] < hi]) for k, (lo, hi) in
                               {'0-10min': (0, 600), '10-20min': (600, 1200), '20min+': (1200, 10 ** 6)}.items()},
                       day1_pairs=fwt.summarize([r for r in rows if r['day1_pair']]),
                       with_ref=fwt.summarize([r for r in rows if r['has_ref']]))
        ws = [r['w_obs_smoke'] for r in rows if r.get('w_obs_smoke') is not None]
        if ws:
            payload['w_obs'] = dict(smoke=float(np.mean(ws)), background=float(np.mean([r['w_obs_bg'] for r in rows])))
    log(tag, **payload)
    return payload


def param_groups(model, use_temporal):
    groups, params = [], []
    for name, p in model.named_parameters():
        new = name.startswith('arbitration') or name.startswith('obs_convs')
        if not p.requires_grad or (new and not use_temporal):
            continue
        mult = 10.0 if ('decode_head' in name or new) else 1.0
        no_decay = 'norm' in name or 'pos_block' in name
        groups.append({'params': [p], 'lr': CFG['base_lr'] * mult, 'lr_multiplier': mult, 'weight_decay': 0.0 if no_decay else 0.01})
        params.append(p)
    return groups, params


def train(name, use_temporal, updates=None, budget=None, init=None):
    model = build(use_temporal, init)
    groups, params = param_groups(model, use_temporal)
    opt = torch.optim.AdamW(groups, lr=CFG['base_lr'], weight_decay=0.01)
    train_set = SmokeSet(train_rows + S5K_TRAIN, train=True, corrupt=use_temporal, neg_share=CFG['train_neg'])
    it = iter(loader(train_set, CFG['micro_batch'], True))
    best, best_state, history, started = -1.0, None, [], time.time()
    paused0, best_update = PAUSED[0], None
    target = updates or CFG['max_updates']
    evals = None
    u = 0
    while u < target:
        u += 1
        model.train(); model.head.G.eval()
        factor = min(u / CFG['warmup'], 1.0) * (1 - (u - 1) / target)
        for g in opt.param_groups:
            g['lr'] = CFG['base_lr'] * g['lr_multiplier'] * factor
        opt.zero_grad(set_to_none=True)
        tot = rel_tot = w_tot = 0.0
        for _ in range(CFG['accumulate']):
            try:
                x, xr, has, valid, y, _ = next(it)
            except StopIteration:
                it = iter(loader(train_set, CFG['micro_batch'], True))
                x, xr, has, valid, y, _ = next(it)
            x, xr, has, valid, y = (t.cuda(non_blocking=True) for t in (x, xr, has, valid, y))
            out, hier, w = model(x, xr if use_temporal else None, has)
            loss = fwt.fosp_loss(model.head, out, hier, y)
            if use_temporal and w:
                rel = (fwt.gate_bce_loss if CFG['gate_loss'] == 'bce' else fwt.reliability_loss)(w, valid, has)
                loss = loss + CFG['rel_weight'] * rel
                rel_tot += float(rel.detach()) / CFG['accumulate']
                w_tot += float(w[-1][:, 1:].detach().mean()) / CFG['accumulate']  # gate on the finest scale
            if not torch.isfinite(loss):
                raise FloatingPointError(f'{name}: non-finite loss at update {u}')
            (loss / CFG['accumulate']).backward()
            tot += float(loss.detach()) / CFG['accumulate']
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        GUARD.step(name, u)
        spu = (time.time() - started - (PAUSED[0] - paused0)) / u
        if budget is not None and u == 20:
            target = int(min(CFG['max_updates'], max(200, budget / spu)))
            log('TARGET_UPDATES', run=name, sec_per_update=round(spu, 3), updates=target)
        if evals is None and u >= 20:
            evals = {int(target * (k + 1) / CFG['n_val_evals']) for k in range(CFG['n_val_evals'])}
        if u == 1 or u % 50 == 0:
            log('TRAIN', run=name, update=u, of=target, loss=round(tot, 5), rel=round(rel_tot, 5), w_obs=float(f'{w_tot:.3g}'),
                sec_per_update=round(spu, 3),
                peak_gb=round(torch.cuda.max_memory_allocated() / 2 ** 30, 2), elapsed_min=round(elapsed() / 60, 1))
        if evals and u in evals:
            rows = evaluate(model, val_rows, use_temporal, name + '_val')
            summ = fwt.summarize(rows)
            ws = [r['w_obs_smoke'] for r in rows if r.get('w_obs_smoke') is not None]
            if ws:  # gate on the observed background (finest scale); ~0 everywhere = branch switched off
                summ.update(w_obs_smoke=float(np.mean(ws)), w_obs_bg=float(np.mean([r['w_obs_bg'] for r in rows if 'w_obs_bg' in r])))
            if val_neg_rows:  # monitoring only: the checkpoint is still chosen by val image-F1
                nr = evaluate(model, val_neg_rows, use_temporal, name + '_valneg', negatives=True)
                summ.update(val_fa_any=float(np.mean([x['fa_any'] for x in nr])), val_fa_200=float(np.mean([x['fa_200'] for x in nr])))
            history.append(dict(update=u, **summ))
            log('VAL', run=name, update=u, **summ)
            if summ['image_f1'] > best:
                best, best_update = summ['image_f1'], u
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                # Save at once, so a crash or a stopped laptop run keeps the best model so far.
                torch.save({'state_dict': best_state, 'history': history, 'cfg': CFG, 'use_temporal': use_temporal,
                            'best_update': u}, OUT / f'{name}_best.pth')
        if elapsed() > TOTAL_BUDGET - 12 * 60:
            log('STOP_EARLY', run=name, update=u, reason='total budget')
            break
    if best_state is None:
        rows = evaluate(model, val_rows, use_temporal, name + '_val')
        history.append(dict(update=u, **fwt.summarize(rows)))
        best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    torch.save({'state_dict': best_state, 'history': history, 'cfg': CFG, 'use_temporal': use_temporal,
                'best_update': best_update or u}, OUT / f'{name}_best.pth')
    log('TRAIN_DONE', run=name, updates=u, best_update=best_update or u, best_val_image_f1=best,
        train_minutes=round((time.time() - started) / 60, 1), paused_minutes=round((PAUSED[0] - paused0) / 60, 1))
    return model, u


results = {}
try:
    log('RUN_CONFIG', runs=RUNS, fixed_updates=FIXED_UPDATES, init=INIT, workers=WORKERS, eval_batch=EVAL_BATCH, out=str(OUT), **CFG)
    n_updates = FIXED_UPDATES
    if 'E0' in RUNS:
        # E0: transfer baseline (no SmokeSeg training)
        m0 = build(False)
        results['E0'] = {s: evaluate(m0, rows, False, 'E0_' + s) for s, rows in (('val', val_rows), ('test', test_rows))}
        results['E0']['neg'] = evaluate(m0, neg_rows, False, 'E0_neg', negatives=True)
        for s in ('val', 'test'):
            report('RESULT_E0_' + s, results['E0'][s])
        report('RESULT_E0_neg', results['E0']['neg'], negatives=True)
        if S5K_TEST:
            results['E0']['s5k'] = evaluate_s5k(m0, S5K_TEST, False, 'E0_s5k')
            report_s5k('RESULT_E0_s5k', results['E0']['s5k'])
        del m0; torch.cuda.empty_cache()

    if 'E1' in RUNS:
        # E1: single-frame fine-tune (sets the update budget for both runs unless FW_UPDATES is given)
        m1, n_updates = train('E1_single', False, updates=FIXED_UPDATES, budget=None if FIXED_UPDATES else E1_TRAIN_BUDGET,
                              init=INIT['E1'])
        results['E1'] = {s: evaluate(m1, rows, False, 'E1_' + s) for s, rows in (('val', val_rows), ('test', test_rows))}
        results['E1']['neg'] = evaluate(m1, neg_rows, False, 'E1_neg', negatives=True)
        for s in ('val', 'test'):
            report('RESULT_E1_' + s, results['E1'][s])
        report('RESULT_E1_neg', results['E1']['neg'], negatives=True)
        if S5K_TEST:
            results['E1']['s5k'] = evaluate_s5k(m1, S5K_TEST, False, 'E1_s5k')
            report_s5k('RESULT_E1_s5k', results['E1']['s5k'])
        del m1; torch.cuda.empty_cache()

    if 'E2' not in RUNS:
        raise SystemExit(0)  # the finally-block still writes bootstrap lines, per_image.json and ALL_DONE
    # E2: same number of updates, observed background + arbitration
    m2, _ = train('E2_temporal', True, updates=n_updates, init=INIT['E2'])
    results['E2'] = {s: evaluate(m2, rows, True, 'E2_' + s) for s, rows in (('val', val_rows), ('test', test_rows))}
    results['E2']['neg'] = evaluate(m2, neg_rows, True, 'E2_neg', negatives=True)
    for s in ('val', 'test'):
        report('RESULT_E2_' + s, results['E2'][s])
    report('RESULT_E2_neg', results['E2']['neg'], negatives=True)
    if S5K_TEST:
        results['E2']['s5k'] = evaluate_s5k(m2, S5K_TEST, True, 'E2_s5k')
        report_s5k('RESULT_E2_s5k', results['E2']['s5k'])
    # Where does the gain come from?  (a) same E2 weights, history switched off -> pure FoSp path;
    # (b) reference taken from a different camera -> reliability should fall back gracefully.
    if elapsed() < TOTAL_BUDGET - 6 * 60:
        results['E2_noref'] = {'test': evaluate(m2, test_rows, True, 'E2_noref', no_ref=True)}
        report('RESULT_E2_noref_test', results['E2_noref']['test'])
    if elapsed() < TOTAL_BUDGET - 4 * 60:
        seqs = sorted({r['seq'] for r in test_rows if r.get('ref')})
        refs = [REF_OF_SEQ[q] for q in seqs]
        swap = {q: refs[(i + len(seqs) // 2) % len(refs)] for i, q in enumerate(seqs)} if len(seqs) > 1 else None
        if swap:
            results['E2_wrongref'] = {'test': evaluate(m2, test_rows, True, 'E2_wrongref', swap_ref=swap)}
            report('RESULT_E2_wrongref_test', results['E2_wrongref']['test'])
finally:
    for a, b in (('E0', 'E1'), ('E1', 'E2'), ('E0', 'E2'), ('E1', 'E2_noref'), ('E1', 'E2_wrongref'), ('E2_noref', 'E2')):
        if a in results and b in results:
            for s in ('val', 'test', 's5k'):
                if s not in results[a] or s not in results[b]:
                    continue
                for key in ('f1', 'iou'):
                    log('BOOTSTRAP', pair=f'{b}-{a}', split=s, metric='image_' + key,
                        **fwt.paired_bootstrap(results[a][s], results[b][s], key=key, group='event'))
            if 'neg' in results[a] and 'neg' in results[b]:  # false alarms on real smoke-free frames (lower is better)
                for key in ('fa_any', 'fa_200'):
                    log('BOOTSTRAP', pair=f'{b}-{a}', split='neg', metric=key,
                        **fwt.paired_bootstrap(results[a]['neg'], results[b]['neg'], key=key, group='event'))
    (OUT / 'per_image.json').write_text(json.dumps(results, default=str))
    log('ALL_DONE', minutes=round(elapsed() / 60, 1), runs=list(results))
