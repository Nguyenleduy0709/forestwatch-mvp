"""Embed fw_temporal.py into train_temporal.py -> build/train_temporal_kaggle.py (paste into one Kaggle cell)."""
from pathlib import Path

here = Path(__file__).resolve().parent
src = (here / 'fw_temporal.py').read_text(encoding='utf-8')
assert "'''" not in src
out = (here / 'train_temporal.py').read_text(encoding='utf-8').replace('__FW_TEMPORAL_SOURCE__', src)
compile(out, 'train_temporal_kaggle.py', 'exec')
(here / 'build').mkdir(exist_ok=True)
(here / 'build' / 'train_temporal_kaggle.py').write_text(out, encoding='utf-8')
print('built', here / 'build' / 'train_temporal_kaggle.py', len(out), 'chars')
