"""Embed fw_temporal.py into train_temporal.py -> build/train_temporal_kaggle.py (paste into one Kaggle cell).

make_kaggle_notebook.py imports build_source() to put the same single-file script into a Kaggle notebook.
"""
from pathlib import Path

here = Path(__file__).resolve().parent


def build_source():
    src = (here / 'fw_temporal.py').read_text(encoding='utf-8')
    assert "'''" not in src
    out = (here / 'train_temporal.py').read_text(encoding='utf-8').replace('__FW_TEMPORAL_SOURCE__', src)
    compile(out, 'train_temporal_kaggle.py', 'exec')
    return out


if __name__ == '__main__':
    out = build_source()
    (here / 'build').mkdir(exist_ok=True)
    (here / 'build' / 'train_temporal_kaggle.py').write_text(out, encoding='utf-8')
    print('built', here / 'build' / 'train_temporal_kaggle.py', len(out), 'chars')
