"""CPU checks for fw_temporal against the official FoSp code (random weights, no GPU).

1. TemporalFoSp(use_temporal=False) must reproduce FOSP.forward exactly when FoSp's z is 0.
2. _mat_input must equal EncoderDecoderFOSP._renormalization (BGR, [-1, 1]).
3. The temporal path runs, starts close to FoSp, and back-propagates into the arbitration
   heads and the decode head (but not into MAT).
4. corrupt_reference / image_cues / metrics behave sensibly.
"""
import sys
import time
import types
from pathlib import Path

import numpy as np
import torch

import os  # noqa: E402
REPO = Path(__file__).resolve().parents[3]
FOSP = Path(os.environ.get('FOSP_DIR', REPO / '.fosp-runtime' / 'FoSp'))   # FoSp @ 02b9fd18
MMCV = Path(os.environ.get('MMCV_DIR', REPO / '.fosp-runtime' / 'mmcv'))   # mmcv v1.6.2 source
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(FOSP), str(FOSP / 'inpainter_setting'), str(MMCV)]
torch.manual_seed(0)
np.random.seed(0)
torch.set_num_threads(4)

# --- make the CUDA-only FoSp code runnable on CPU for this test
torch.Tensor.cuda = lambda self, *a, **k: self
_orig_to = torch.nn.Module.to
torch.nn.Module.to = lambda self, *a, **k: self if (a and isinstance(a[0], torch.device) and a[0].type == 'cuda') else _orig_to(self, *a, **k)

from mmcv import Config  # noqa: E402
from mmseg.models import build_segmentor  # noqa: E402
from inpainter_setting.torch_utils.ops import bias_act, upfirdn2d  # noqa: E402
from torch_utils.ops import bias_act as ba, upfirdn2d as uf  # noqa: E402
for op in (bias_act, upfirdn2d, ba, uf):
    op._init = lambda: False
import mmseg.models.decode_heads.FOSP_main as fosp_main  # noqa: E402


class _TorchProxy(types.ModuleType):
    def __getattr__(self, name):
        return getattr(torch, name)


proxy = _TorchProxy('torch')
proxy.device = lambda *a, **k: torch.device('cpu')
fosp_main.torch = proxy


class _NpProxy(types.ModuleType):
    def __getattr__(self, name):
        return getattr(np, name)


np_proxy = _NpProxy('numpy')
np_proxy.random = types.SimpleNamespace(randn=lambda *s: np.zeros(s))  # FoSp's z := 0
fosp_main.np = np_proxy

import fw_temporal as fwt  # noqa: E402

cfg = Config.fromfile(str(FOSP / 'config/fosp_config.py'))
cfg.model.pretrained = None
t = time.time()
seg = build_segmentor(cfg.model)
seg.eval()
print('built FoSp on CPU in', round(time.time() - t, 1), 's; params (M):', round(sum(p.numel() for p in seg.parameters()) / 1e6, 2))

mean = np.array([123.675, 116.28, 103.53])
std = np.array([58.395, 57.12, 57.375])
rgb = np.random.randint(0, 256, size=(1, 512, 512, 3)).astype(np.float32)
x = torch.from_numpy(((rgb - fwt.MEAN) / fwt.STD).transpose(0, 3, 1, 2).copy())

wrapper = fwt.TemporalFoSp(seg, num_refs=1, use_temporal=False).eval()
with torch.no_grad():
    ren = seg._renormalization(x, mean=mean, std=std)
    mine = wrapper._mat_input(x)
    print('check 2  |_mat_input - _renormalization| max =', float((ren - mine).abs().max()))
    assert float((ren - mine).abs().max()) < 1e-4

    t = time.time()
    ref_out, ref_hier = seg.decode_head.forward(seg.extract_feat(x), ren)
    print('official FOSP.forward on CPU:', round(time.time() - t, 1), 's; out', tuple(ref_out.shape))
    t = time.time()
    my_out, my_hier, w = wrapper(x)
    print('wrapper forward:', round(time.time() - t, 1), 's')
    d = float((ref_out - my_out).abs().max())
    dh = max(float((a - b).abs().max()) for a, b in zip(ref_hier, my_hier))
    print('check 1  max|out diff| =', d, ' max|hier diff| =', dh, ' weights:', w)
    assert d < 1e-4 and dh < 1e-4

# --- temporal path
temporal = fwt.TemporalFoSp(seg, num_refs=1, use_temporal=True)
temporal.train()
temporal.head.G.eval()
ref_rgb = np.clip(rgb + np.random.normal(0, 8, rgb.shape), 0, 255).astype(np.float32)
xr = torch.from_numpy(((ref_rgb - fwt.MEAN) / fwt.STD).transpose(0, 3, 1, 2).copy())[:, None]
x2 = torch.cat([x, x], 0)
xr2 = torch.cat([xr, xr], 0)
has = torch.tensor([1.0, 0.0])
y = torch.zeros(2, 1, 512, 512, dtype=torch.long)
y[:, :, 200:260, 100:180] = 1
valid = torch.ones(2, 1, 512, 512)
valid[0, :, 180:280, 80:200] = 0
t = time.time()
out, hier, w = temporal(x2, xr2, has)
print('temporal forward (B=2):', round(time.time() - t, 1), 's; out', tuple(out.shape), '; weight maps', [tuple(a.shape) for a in w])
print('check 3a init weight on inferred source (should be ~0.98):', [round(float(a[0, 0].mean()), 4) for a in w])
print('check 3b sample without history falls back to FoSp exactly:', all(float(a[1, 0].min()) == 1.0 for a in w))
assert all(float(a[1, 0].min()) == 1.0 for a in w)
loss = fwt.fosp_loss(temporal.head, out, hier, y)
rel = fwt.reliability_loss(w, valid, has)
(loss + 0.5 * rel).backward()
g_arb = [p.grad for p in temporal.arbitration.parameters()]
g_head = [p.grad for n, p in temporal.named_parameters() if n.startswith('seg.decode_head.fusion') and p.requires_grad]
g_mat = [p.grad for p in temporal.head.G.parameters()]
print('check 3c loss', round(float(loss), 4), 'rel', round(float(rel), 6),
      '| arbitration grads non-zero:', sum(int(g is not None and float(g.abs().sum()) > 0) for g in g_arb), '/', len(g_arb),
      '| fusion grads:', sum(int(g is not None) for g in g_head), '/', len(g_head),
      '| MAT grads (must be 0):', sum(int(g is not None) for g in g_mat))
assert all(g is None for g in g_mat)
assert any(g is not None and float(g.abs().sum()) > 0 for g in g_arb)

# --- residual / delta fusion: exactly FoSp at initialisation, yet gradients reach the new 1x1 convs
for fusion in ('residual', 'delta'):
    seg.eval()  # train-mode forwards update the shared BatchNorm statistics, so re-take FoSp's output each time
    with torch.no_grad():
        base_out, _, _ = wrapper(x)
    tf = fwt.TemporalFoSp(seg, num_refs=1, use_temporal=True, init_bias=0.0, fusion=fusion).eval()
    with torch.no_grad():
        o, _, wf = tf(x, xr, torch.ones(1))
    d = float((o - base_out).abs().max())
    tf.train(); tf.head.G.eval()
    for p in tf.parameters():
        p.grad = None
    o2, h2, _ = tf(x2, xr2, has)
    fwt.fosp_loss(tf.head, o2, h2, y).backward()
    g_obs = [float(c.weight.grad.abs().sum()) for c in tf.obs_convs]
    print(f'check 3d {fusion}: max|out - FoSp| at init = {d:.2e}, gate = {float(wf[0][0, 1].mean()):.3f}, '
          f'obs_conv grad L1 = {[f"{g:.2e}" for g in g_obs]}')
    assert d < 1e-4 and all(g > 0 for g in g_obs)
    seg.eval()

# --- input-level baselines (E3 concat, E4 diff): exactly FoSp at init, gradients reach the extra stem conv,
#     and a sample without history stays exactly FoSp even after the extra conv has changed
for kind in ('concat', 'diff'):
    seg.eval()
    with torch.no_grad():
        base_out, _, _ = wrapper(x)
    tb = fwt.TemporalFoSp(seg, num_refs=1, use_temporal=False, input_fusion=kind).eval()
    with torch.no_grad():
        o, _, _ = tb(x, xr, torch.ones(1))
    d = float((o - base_out).abs().max())
    tb.train(); tb.head.G.eval()
    for p in tb.parameters():
        p.grad = None
    o2, h2, _ = tb(x2, xr2, has)
    fwt.fosp_loss(tb.head, o2, h2, y).backward()
    g = float(tb.extra_stem.weight.grad.abs().sum())
    with torch.no_grad():
        tb.extra_stem.weight.normal_(0, 0.01)
    seg.eval(); tb.eval()
    with torch.no_grad():
        base2, _, _ = wrapper(x)
        o_noref, _, _ = tb(x, xr, torch.zeros(1))
        o_ref, _, _ = tb(x, xr, torch.ones(1))
    d0, d1 = float((o_noref - base2).abs().max()), float((o_ref - base2).abs().max())
    print(f'check 3e {kind}: stem conv {tuple(tb.extra_stem.weight.shape)}, max|out - FoSp| at init = {d:.2e}, '
          f'extra-stem grad L1 = {g:.2e}, no-history after update = {d0:.2e}, with history after update = {d1:.2e}')
    assert d < 1e-4 and g > 0 and d0 < 1e-4 and d1 > 1e-6
    seg.eval()

# --- corruptions / cues / metrics
rng = np.random.default_rng(0)
cur = (np.random.rand(512, 512, 3) * 255).astype(np.uint8)
ref = cur.copy()
m = np.zeros((512, 512), bool)
m[100:150, 100:200] = True
kinds_seen = {}
for i in range(200):
    r2, v, kinds = fwt.corrupt_reference(cur, ref, m, (cur, m.astype(np.uint8)), rng)
    assert r2.shape == ref.shape and r2.dtype == np.uint8 and v.shape == (512, 512) and set(np.unique(v)) <= {0, 1}
    for k in kinds:
        kinds_seen[k] = kinds_seen.get(k, 0) + 1
    if kinds == ['self']:
        assert v[m].max() == 0 and v[~m].mean() > 0.9
print('check 4a corruption kinds over 200 draws:', kinds_seen)
c = torch.rand(1, 3, 64, 64)
cues_same = fwt.image_cues(c, c)
cues_shift = fwt.image_cues(c, torch.roll(c, 5, dims=3))
print('check 4b cues identical: r=%.4f g=%.3f | shifted: r=%.4f g=%.3f' % (
    float(cues_same[:, 0].mean()), float(cues_same[:, 1].mean()), float(cues_shift[:, 0].mean()), float(cues_shift[:, 1].mean())))
assert float(cues_same[:, 1].mean()) > 0.99 and float(cues_shift[:, 1].mean()) < 0.5
pa = [dict(id=str(i), event=str(i % 5), f1=0.4, iou=0.3) for i in range(50)]
pb = [dict(id=str(i), event=str(i % 5), f1=0.45, iou=0.33) for i in range(50)]
print('check 4c bootstrap:', fwt.paired_bootstrap(pa, pb, n=200))
# two-sided gate loss: gradient pulls the observed weight up on valid pixels, down on invalid ones, ignores no-ref samples
logits = torch.zeros(2, 2, 8, 8, requires_grad=True)
wg = torch.softmax(logits, 1)
vg = torch.ones(2, 1, 8, 8)
vg[:, :, :4] = 0
lg = fwt.gate_bce_loss([wg], vg, torch.tensor([1.0, 0.0]))
lg.backward()
g1 = logits.grad[0, 1]
print('check 4d gate_bce_loss %.4f | grad on observed logit: invalid %+.4f, valid %+.4f, no-ref sample %.1f' % (
    float(lg), float(g1[:4].mean()), float(g1[4:].mean()), float(logits.grad[1].abs().sum())))
assert float(g1[:4].mean()) > 0 > float(g1[4:].mean()) and float(logits.grad[1].abs().sum()) == 0
# per-image soft-F1 loss: ~0 for a perfect mask, ~1 when the smoke is missed, ignore pixels do not count,
# and its gradient raises the logit on smoke and lowers it on background
ys = torch.zeros(2, 1, 64, 64, dtype=torch.long)
ys[0, :, 10:20, 10:20] = 1
ys[0, :, 40:50, 40:50] = 255
perfect = torch.where(ys == 1, 20.0, -20.0).float()
missed = torch.full((2, 1, 64, 64), -20.0)
l_perfect, l_missed = float(fwt.soft_f1_loss(perfect, ys)), float(fwt.soft_f1_loss(missed[:1], ys[:1]))
noisy = perfect.clone()
noisy[ys == 255] = 20.0  # confident "smoke" on ignored pixels must not be penalised
l_ignore = float(fwt.soft_f1_loss(noisy, ys))
lg2 = torch.zeros(1, 1, 64, 64, requires_grad=True)
fwt.soft_f1_loss(lg2, ys[:1]).backward()
gs, gb = float(lg2.grad[0, 0][ys[0, 0] == 1].mean()), float(lg2.grad[0, 0][ys[0, 0] == 0].mean())
print('check 4e soft_f1_loss perfect %.4f, missed %.4f, ignore-only noise %.4f | grad smoke %+.2e, background %+.2e' % (
    l_perfect, l_missed, l_ignore, gs, gb))
assert l_perfect < 0.01 and l_missed > 0.95 and abs(l_ignore - l_perfect) < 1e-6 and gs < 0 < gb
print('ALL CPU CHECKS PASSED')
