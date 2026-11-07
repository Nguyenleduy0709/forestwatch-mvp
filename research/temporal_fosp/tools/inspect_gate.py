"""Inspect the reliability gate and residual convs of a saved E2 checkpoint (CPU, no model build).
Usage: python -I research/temporal_fosp/tools/inspect_gate.py <E2_temporal_best.pth>
The gate is softmax over [inferred, observed]; its last conv starts at weight 0, bias [b0, 0].
A large negative (bias1 - bias0) with small weights means w_obs ~ sigmoid(bias1 - bias0) everywhere.
"""
import sys

import torch

ck = torch.load(sys.argv[1], map_location='cpu', mmap=True, weights_only=False)
sd = ck.get('state_dict', ck.get('model', ck)) if isinstance(ck, dict) else ck
print('top-level keys:', [k for k in ck.keys()][:8] if isinstance(ck, dict) else type(ck))
for k in ('best_update', 'update', 'val'):
    if isinstance(ck, dict) and k in ck and not isinstance(ck[k], dict):
        print(k, ck[k])
for i in range(4):
    wk, bk = f'arbitration.{i}.net.4.weight', f'arbitration.{i}.net.4.bias'
    if wk not in sd:
        continue
    w, b = sd[wk].float(), sd[bk].float()
    d = b[1] - b[0]
    print(f'scale {i}: bias1-bias0 = {float(d):+.3f} -> sigmoid = {float(torch.sigmoid(d)):.2e}; '
          f'|W1-W0| L1 = {float((w[1] - w[0]).abs().sum()):.3f}, max = {float((w[1] - w[0]).abs().max()):.3f}')
for i in range(4):
    wk = f'obs_convs.{i}.weight'
    if wk in sd:
        w = sd[wk].float()
        print(f'obs_conv {i}: shape {tuple(w.shape)}, |W| mean {float(w.abs().mean()):.2e}, max {float(w.abs().max()):.2e}, '
              f'|b| max {float(sd[f"obs_convs.{i}.bias"].float().abs().max()):.2e}')
for i in range(4):
    keys = [k for k in sd if k.startswith(f'seg.decode_head.foreground_channel_convs.{i}.') and k.endswith('conv.weight')]
    for k in keys:
        w = sd[k].float()
        print(f'FoSp fg_conv {i}: shape {tuple(w.shape)}, |W| mean {float(w.abs().mean()):.2e}, max {float(w.abs().max()):.2e}')
