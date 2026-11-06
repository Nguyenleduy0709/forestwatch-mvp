"""Audit sequence-level leakage between two FIgLib/SmokeSeg-style split lists.

Usage:
  python split_leakage.py TRAIN_LIST TEST_LIST
Each line is an image id like
  20200813_Ranch2Fire_marconi-n-mobo-c__1597358362_+00480
  -> event '20200813_Ranch2Fire', camera 'marconi-n-mobo-c', station 'marconi',
     unix time 1597358362, offset +480 s from ignition (negative = pre-ignition).
"""
import sys, statistics as st
from collections import defaultdict

def load(path):
    out, bad = [], 0
    for line in open(path):
        s = line.strip().split("/")[-1].rsplit(".", 1)[0] if line.strip() else ""
        if "__" not in s:
            bad += int(bool(s)); continue
        seq, rest = s.split("__", 1)
        ts, off = rest.split("_")[:2]
        parts = seq.split("_")
        cam = parts[-1]
        out.append(dict(event="_".join(parts[:-1]), date=parts[0], cam=cam,
                        station=cam.split("-")[0], seq=seq, ts=int(ts), off=int(off)))
    return out, bad

def pct(a, b):
    return f"{a}/{b} ({100*a/max(b,1):.1f}%)"

tr, bad_tr = load(sys.argv[1]); te, bad_te = load(sys.argv[2])
print(f"train images={len(tr)} (unparsed {bad_tr}), test images={len(te)} (unparsed {bad_te})")
for nm, S in (("train", tr), ("test", te)):
    print(f"  {nm}: events={len({x['event'] for x in S})} stations={len({x['station'] for x in S})} "
          f"cameras={len({x['cam'] for x in S})} sequences={len({x['seq'] for x in S})} "
          f"pre-ignition frames={sum(x['off'] < 0 for x in S)}")
seq_ts = defaultdict(list)
for x in tr: seq_ts[x["seq"]].append(x["ts"])
for key in ("seq", "event", "date", "station", "cam"):
    keys = {x[key] for x in tr}
    print(f"test images sharing {key:7s} with train: {pct(sum(x[key] in keys for x in te), len(te))}")
gaps = [min(abs(x["ts"] - t) for t in seq_ts[x["seq"]]) / 60 for x in te if x["seq"] in seq_ts]
if gaps:
    print(f"nearest train frame of the same sequence: median {st.median(gaps):.1f} min; "
          f"<=1 min {sum(g <= 1.01 for g in gaps)}, <=2 min {sum(g <= 2.01 for g in gaps)}, "
          f"<=5 min {sum(g <= 5.01 for g in gaps)}")
