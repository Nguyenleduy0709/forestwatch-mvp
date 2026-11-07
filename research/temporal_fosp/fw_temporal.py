"""FoSp-Temporal: arbitrate between FoSp's *inferred* background (MAT inpainting) and an
*observed* background (pre-ignition reference of the same fixed camera).

FoSp builds its foreground features as 10*|MAT(I, focus-mask) - MAT(I, no mask)|, i.e. the
difference to a hallucinated smoke-free background.  With a fixed camera we can also
compare against a real past frame B:  10*|MAT(B, no mask) - MAT(I, no mask)|.  A small
reliability head predicts, per pixel and per scale, softmax weights over the sources.
The weights are supervised only where we *know* the reference is wrong (synthetic
misalignment or smoke pasted into the reference), see `reliability_loss`.

The wrapper reuses every module of an official EncoderDecoderFOSP (mmseg 0.x) model; with
`use_temporal=False` it reproduces FoSp exactly, except that the MAT latent z is fixed
(z = 0) instead of being re-sampled at every forward pass.
"""
import math
import random

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)


# --------------------------------------------------------------------------- modules
class Arbitration(nn.Module):
    """Per-pixel softmax weights over background sources (index 0 = inferred/inpainting)."""

    def __init__(self, n_sources, cue_channels, hidden=32, init_bias=4.0):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(cue_channels, hidden, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(hidden, hidden, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(hidden, n_sources, 1))
        nn.init.zeros_(self.net[-1].weight)
        with torch.no_grad():
            self.net[-1].bias.zero_()
            self.net[-1].bias[0] = init_bias  # start (almost) exactly as FoSp

    def forward(self, cues):
        return torch.softmax(self.net(cues), dim=1)


def _gray(x):
    return (0.299 * x[:, 0:1] + 0.587 * x[:, 1:2] + 0.114 * x[:, 2:3])


def image_cues(cur, ref, box=9, eps=1e-6):
    """Cheap reliability cues at full resolution; cur/ref are RGB in [0, 1], (B,3,H,W).

    r: residual after a global per-channel gain/offset fit of ref to cur (illumination change
       is explained away, local change is not);
    g: local gradient-orientation agreement in a box window (misalignment destroys it,
       thin smoke mostly attenuates magnitude).
    """
    b, c = cur.shape[:2]
    x = ref.reshape(b, c, -1)
    y = cur.reshape(b, c, -1)
    xm, ym = x.mean(-1, keepdim=True), y.mean(-1, keepdim=True)
    gain = ((x - xm) * (y - ym)).mean(-1, keepdim=True) / ((x - xm).pow(2).mean(-1, keepdim=True) + eps)
    gain = gain.clamp(0.25, 4.0)
    fit = gain * (x - xm) + ym
    r = (y - fit).abs().mean(1, keepdim=True).reshape(b, 1, *cur.shape[2:])
    kx = torch.tensor([[-1., 0., 1.], [-2., 0., 2.], [-1., 0., 1.]], device=cur.device).view(1, 1, 3, 3) / 8
    ky = kx.transpose(2, 3)
    gi, gr = _gray(cur), _gray(ref)
    ix, iy = F.conv2d(gi, kx, padding=1), F.conv2d(gi, ky, padding=1)
    rx, ry = F.conv2d(gr, kx, padding=1), F.conv2d(gr, ky, padding=1)
    pool = lambda t: F.avg_pool2d(t, box, stride=1, padding=box // 2)
    num = pool(ix * rx + iy * ry)
    den = torch.sqrt(pool(ix * ix + iy * iy) * pool(rx * rx + ry * ry)) + eps
    g = num / den
    return torch.cat([r * 4.0, g], dim=1)  # (B,2,H,W)


class TemporalFoSp(nn.Module):
    """fusion='convex' (Day-2 pilot): FoSp's foreground conv sees w0*F_inferred + w1*F_observed.
    fusion='residual': FoSp's path is kept as is and the observed difference is *added* after FoSp's
    foreground conv through a zero-initialised 1x1 conv, scaled by the reliability gate w1.  At
    initialisation the model is exactly FoSp, yet the new conv receives gradients (in the convex
    pilot, w1 fell to ~1e-6).  The gate w1 is still a softmax and can still saturate towards 0, so
    check w_obs after training."""

    def __init__(self, segmentor, num_refs=1, use_temporal=True, hidden=32, init_bias=4.0, fusion='convex'):
        super().__init__()
        assert fusion in ('convex', 'residual'), fusion
        self.seg = segmentor
        self.head = segmentor.decode_head
        self.num_refs = num_refs
        self.use_temporal = use_temporal
        self.fusion = fusion
        n_src = 1 + num_refs
        cue_ch = n_src + 1 + 2 * num_refs + 1   # |dF| magnitudes, focus map, image cues, has_ref
        self.arbitration = nn.ModuleList(
            [Arbitration(n_src, cue_ch, hidden, init_bias) for _ in range(len(self.head.in_channels))])
        if fusion == 'residual':
            self.obs_convs = nn.ModuleList()
            for fc in self.head.foreground_channel_convs:
                conv = nn.Conv2d(fc.conv.in_channels * num_refs, fc.conv.out_channels, 1)
                nn.init.zeros_(conv.weight)
                nn.init.zeros_(conv.bias)
                self.obs_convs.append(conv)
        mean = torch.tensor(MEAN).view(1, 3, 1, 1)
        std = torch.tensor(STD).view(1, 3, 1, 1)
        self.register_buffer('mean', mean, persistent=False)
        self.register_buffer('std', std, persistent=False)

    # FoSp feeds MAT with mmcv.imdenormalize(..., to_bgr=True) / 127.5 - 1, i.e. BGR in [-1, 1].
    def _mat_input(self, x_norm):
        rgb = x_norm * self.std + self.mean
        return rgb[:, [2, 1, 0]] / 127.5 - 1.0

    def _rgb01(self, x_norm):
        return ((x_norm * self.std + self.mean) / 255.0).clamp(0, 1)

    def _mat(self, img, mask, z, label):
        return self.head.generate_images(self.head.G, img, mask, z, label, 'none')

    def forward(self, x, refs=None, has_ref=None):
        """x: (B,3,512,512) normalised RGB; refs: (B,K,3,512,512) normalised RGB; has_ref: (B,) {0,1}."""
        head = self.head
        n_in = len(head.in_channels)
        feats = head._transform_inputs(self.seg.extract_feat(x))
        focus_map, focus_logits, out_1_4, out_1_8, out_1_16, out_1_32 = head.BCG(feats)
        focus_mask = ((1 - focus_map) >= head.focus_threshold).float()
        bsz = x.shape[0]
        label = torch.zeros([bsz, head.G.c_dim], device=x.device)
        z = torch.zeros([bsz, head.G.z_dim], device=x.device)
        ones = torch.ones_like(focus_mask)
        mat_img = self._mat_input(x)
        with torch.no_grad():
            inpaint = self._mat(mat_img, focus_mask, z, label)
            origin = self._mat(mat_img, ones, z, label)
        fg = [(inpaint[i] - origin[i]).abs() * 10 for i in range(len(inpaint))]
        weights, obs_terms = None, None
        if self.use_temporal and refs is not None:
            if has_ref is None:
                has_ref = torch.ones(bsz, device=x.device)
            hr = has_ref.float().view(bsz, 1, 1, 1)
            obs, cues_full = [], []
            with torch.no_grad():
                for k in range(refs.shape[1]):
                    ref_feat = self._mat(self._mat_input(refs[:, k]), ones, z, label)
                    obs.append([(ref_feat[i] - origin[i]).abs() * 10 for i in range(len(origin))])
                    cues_full.append(image_cues(self._rgb01(x), self._rgb01(refs[:, k])))
            cues_full = torch.cat(cues_full, 1)
            weights, fused = [], []
            for i in range(len(fg)):
                cands = [fg[i]] + [o[i] for o in obs]
                size = fg[i].shape[2:]
                mags = [torch.log1p(cnd.mean(1, keepdim=True)) for cnd in cands]
                fm = F.interpolate(focus_map, size=size, mode='bilinear', align_corners=False)
                ic = F.adaptive_avg_pool2d(cues_full, size)
                cue = torch.cat(mags + [fm, ic, hr.expand(-1, 1, *size)], 1)
                w = self.arbitration[i](cue)
                # Hard fallback to FoSp when the camera has no usable history.
                onehot = torch.zeros_like(w)
                onehot[:, 0] = 1
                w = hr * w + (1 - hr) * onehot
                weights.append(w)
                if self.fusion == 'convex':
                    fused.append(sum(w[:, k:k + 1] * cands[k] for k in range(len(cands))))
                else:
                    fused.append(torch.cat([w[:, k:k + 1] * cands[k] for k in range(1, len(cands))], 1))
            if self.fusion == 'convex':
                fg = fused
            else:
                obs_terms = fused
        foreground = [head.foreground_channel_convs[i](fg[i]) for i in range(len(fg))]
        if obs_terms is not None:
            foreground = [foreground[i] + self.obs_convs[i](obs_terms[i]) for i in range(len(foreground))]
        # FoSp always re-registers the foreground scale to the backbone feature it is fused with
        # (its `shape[2:] != [512, 512]` test compares a tuple with a list, so it is always True).
        foreground = [F.interpolate(foreground[i], size=feats[n_in - 1 - i].shape[2:], mode=head.interpolate_mode,
                                    align_corners=head.align_corners) for i in range(len(foreground))]
        temp = None
        for idx in range(n_in):
            feats[n_in - 1 - idx] = head.add_query(feats[n_in - 1 - idx], focus_map)
            h = torch.cat([feats[n_in - 1 - idx], foreground[idx]], dim=1)
            conv = head.fusion_foreground_inputs_conv[idx]
            if idx > 0:
                h = torch.cat([h, temp], dim=1)
            temp = conv(h)
            if idx < n_in - 1:
                temp = F.interpolate(temp, size=feats[n_in - 1 - idx - 1].shape[2:],
                                     mode=head.interpolate_mode, align_corners=head.align_corners)
        out = head.cls_fusion_seg(head.fusion_final_conv(temp))
        return out, [out_1_4, out_1_8, out_1_16, out_1_32, focus_logits], weights


# --------------------------------------------------------------------------- losses
def fosp_loss(head, out, hier, y):
    """Official FoSp objective: 0.5*BCE(final) + 0.1*BCE(each of 5 hierarchical logits)."""
    name = head.loss_decode.loss_name
    loss = 0.5 * head.losses(out, y)[name]
    for h in hier:
        loss = loss + 0.1 * head.losses(h, y)[name]
    return loss


def reliability_loss(weights, valid, has_ref):
    """Mean weight put on observed sources at pixels known to be invalid.

    weights: list over scales of (B,1+K,h,w); valid: (B,K,H,W) in {0,1} (1 = valid or
    unknown); has_ref: (B,).  Down-sampling is conservative (a cell is invalid if any pixel is).
    """
    if not weights:
        return valid.new_zeros((), dtype=torch.float32)
    total = 0.0
    hr = has_ref.float().view(-1, 1, 1, 1)
    for w in weights:
        inv = F.adaptive_max_pool2d(1.0 - valid.float(), w.shape[2:])
        total = total + (inv * w[:, 1:] * hr).mean()
    return total / len(weights)


# --------------------------------------------------------------------------- corruptions (numpy)
def _affine_validity(m_inv, h, w, tol):
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = m_inv[0, 0] * xs + m_inv[0, 1] * ys + m_inv[0, 2]
    sy = m_inv[1, 0] * xs + m_inv[1, 1] * ys + m_inv[1, 2]
    disp = np.sqrt((sx - xs) ** 2 + (sy - ys) ** 2)
    inside = (sx >= 0) & (sx <= w - 1) & (sy >= 0) & (sy <= h - 1)
    return ((disp <= tol) & inside).astype(np.uint8)


def corrupt_reference(cur, ref, cur_mask, donor, rng, p_shift=0.25, p_paste=0.25, p_self=0.15,
                      p_photo=0.5, max_shift=20.0, tol=3.0):
    """Return (corrupted_ref, validity, kinds). cur/ref/donor images are uint8 HxWx3 RGB.

    validity = 1 where the reference is valid (or unknown), 0 where we made it invalid.
    """
    import cv2
    h, w = ref.shape[:2]
    ref = ref.copy()
    valid = np.ones((h, w), np.uint8)
    kinds = []
    if rng.random() < p_photo:
        x = ref.astype(np.float32) / 255.0
        x = x ** rng.uniform(0.8, 1.25)
        x = x * rng.uniform(0.85, 1.15) * rng.uniform(0.94, 1.06, size=3)
        x = (x - x.mean()) * rng.uniform(0.85, 1.15) + x.mean()
        ref = np.clip(x * 255.0, 0, 255).astype(np.uint8)
        kinds.append('photo')
    if rng.random() < p_shift:
        ang = rng.uniform(-1.0, 1.0)
        sc = rng.uniform(0.97, 1.03)
        m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, sc)
        m[:, 2] += rng.uniform(-max_shift, max_shift, size=2)
        ref = cv2.warpAffine(ref, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        valid &= _affine_validity(cv2.invertAffineTransform(m), h, w, tol)
        kinds.append('shift')
    if donor is not None and rng.random() < p_paste:
        d_img, d_mask = donor
        dy, dx = rng.integers(-128, 129, size=2)
        m = np.float32([[1, 0, dx], [0, 1, dy]])
        d_img = cv2.warpAffine(d_img, m, (w, h), borderMode=cv2.BORDER_REFLECT)
        d_mask = cv2.warpAffine(d_mask.astype(np.float32), m, (w, h), borderValue=0)
        soft = cv2.GaussianBlur(d_mask, (0, 0), 2.0)[..., None] * rng.uniform(0.5, 1.0)
        ref = (ref * (1 - soft) + d_img * soft).astype(np.uint8)
        valid &= (cv2.dilate((d_mask > 0.5).astype(np.uint8), np.ones((9, 9), np.uint8)) == 0).astype(np.uint8)
        kinds.append('paste')
    if cur_mask is not None and cur_mask.any() and rng.random() < p_self:
        soft = cv2.GaussianBlur(cur_mask.astype(np.float32), (0, 0), 2.0)[..., None] * rng.uniform(0.3, 0.9)
        ref = (ref * (1 - soft) + cur.astype(np.float32) * soft).astype(np.uint8)
        valid &= (cv2.dilate(cur_mask.astype(np.uint8), np.ones((9, 9), np.uint8)) == 0).astype(np.uint8)
        kinds.append('self')
    return ref, valid, kinds


# --------------------------------------------------------------------------- metrics
def image_scores(pred, gt, ignore=None):
    """FoSp-style per-image scores (beta = 1) plus counts; pred/gt boolean arrays."""
    if ignore is not None:
        keep = ~ignore
        pred, gt = pred[keep], gt[keep]
    tp = int(np.count_nonzero(pred & gt))
    fp = int(np.count_nonzero(pred & ~gt))
    fn = int(np.count_nonzero(~pred & gt))
    return dict(tp=tp, fp=fp, fn=fn, f1=2 * tp / max(2 * tp + fp + fn, 1),
                iou=tp / max(tp + fp + fn, 1), precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1),
                gt_frac=(tp + fn) / max(gt.size, 1))


def size_bucket(gt_frac):
    return 'small' if gt_frac < 0.005 else 'medium' if gt_frac <= 0.025 else 'large'


def summarize(rows):
    if not rows:
        return {}
    tp, fp, fn = (sum(r[k] for r in rows) for k in ('tp', 'fp', 'fn'))
    return dict(n=len(rows), image_f1=float(np.mean([r['f1'] for r in rows])),
                image_iou=float(np.mean([r['iou'] for r in rows])),
                pixel_f1=2 * tp / max(2 * tp + fp + fn, 1), pixel_iou=tp / max(tp + fp + fn, 1),
                precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1))


def paired_bootstrap(a_rows, b_rows, key='f1', group='event', n=2000, seed=0):
    """CI for mean(b) - mean(a) over images, resampling groups (events) with replacement."""
    by = {}
    for ra, rb in zip(a_rows, b_rows):
        assert ra['id'] == rb['id']
        by.setdefault(ra[group], []).append(rb[key] - ra[key])
    groups = list(by)
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(n):
        pick = rng.integers(0, len(groups), len(groups))
        diffs = [d for g in pick for d in by[groups[g]]]
        stats.append(np.mean(diffs))
    diffs_all = [d for g in groups for d in by[g]]
    return dict(delta=float(np.mean(diffs_all)), ci95=[float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))],
                n_groups=len(groups), p_le_0=float(np.mean(np.array(stats) <= 0)))
