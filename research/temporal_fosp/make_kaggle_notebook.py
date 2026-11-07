"""Build a Kaggle notebook folder (kernel-metadata.json + .ipynb) that runs train_temporal.py on one T4.

The notebook writes the single-file script (build_kaggle_script.build_source) to /kaggle/working/fw_train.py,
runs a smoke pass first (8 rows per split, 20 updates) so a broken input fails within minutes, then the full run.
Push it with:  kaggle kernels push -p <out>   (the notebook is private; results land in its Output).

Day-3 SMOKE5K arms (same code and budget, only the training data differs):
  python research/temporal_fosp/make_kaggle_notebook.py --preset day3-s5k-a --out work/kaggle_push/day3_s5k_a
  python research/temporal_fosp/make_kaggle_notebook.py --preset day3-s5k-b --out work/kaggle_push/day3_s5k_b

Continue from a saved model (weights only: the optimizer and the LR schedule start again):
  python research/temporal_fosp/make_kaggle_notebook.py --title "ForestWatch Day4 E1 continue" \
      --source nguynlduyb24dckh043/forestwatch-day3-s5k-b \
      --env FW_RUNS=E1 --env FW_S5K_TRAIN=1 --env FW_INIT_E1=day3_s5k_b/E1_single_best.pth --out work/kaggle_push/day4
FW_INIT_E1 / FW_INIT_E2 may be a path or a pattern searched under /kaggle/input.

Day-4 presets (after the Day-3 arms finish):
  --preset day4-tta-a / day4-tta-b   no training: load the arm's best E1, choose (test-time mode, threshold) on val,
                                     report test / false alarms / SMOKE5K at that choice (about 1 GPU hour)
  --preset day4-dice-zoom            E0 + E1 trained with the per-image soft-F1 loss and zoomed crops, plus the sweep;
                                     add the winning arm's data settings, e.g. --env FW_S5K_TRAIN=1 --env FW_TRAIN_NEG=0.2
  --preset day4-e2 / day4-e3 / day4-e4   one 6000-update run each, same data settings as the E1 they are compared with
                                     (tools/compare_runs.py across notebooks): E2 observed background with the
                                     two-sided gate and delta fusion; E3 reference as input channels; E4 |image - reference|
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_kaggle_script import build_source  # noqa: E402

USER = 'nguynlduyb24dckh043'
# FoSp-official + mmcv + deps (+ SMOKE5K), FoSp 14k checkpoint + SMOKE5K, and the Day-2 split/references.
SOURCES = [f'{USER}/forestwatch-fosp-smoke5k', f'{USER}/notebook32e4daa033', f'{USER}/forestwatch-day2-temporal-prep']
BASE = dict(FW_RUNS='E0,E1', FW_S5K_EVAL='1', FW_N_VAL_EVALS='8', FW_UPDATES='6000', FW_TOTAL_BUDGET='39600')
PRESETS = {  # name: (title, description, FW_* settings, extra kernel outputs to attach)
    'day3-s5k-a': ('ForestWatch Day3 S5K A', 'SmokeSeg only', {}, []),
    'day3-s5k-b': ('ForestWatch Day3 S5K B', 'SmokeSeg + SMOKE5K', {'FW_S5K_TRAIN': '1'}, []),
    'day4-tta-a': ('ForestWatch Day4 TTA A', 'eval only: val-chosen test-time mode and threshold for arm A',
                   {'FW_RUNS': 'E1', 'FW_EVAL_ONLY': '1', 'FW_INIT_E1': 'day3_s5k_a/E1_single_best.pth'},
                   [f'{USER}/forestwatch-day3-s5k-a']),
    'day4-tta-b': ('ForestWatch Day4 TTA B', 'eval only: val-chosen test-time mode and threshold for arm B',
                   {'FW_RUNS': 'E1', 'FW_EVAL_ONLY': '1', 'FW_INIT_E1': 'day3_s5k_b/E1_single_best.pth'},
                   [f'{USER}/forestwatch-day3-s5k-b']),
    'day4-dice-zoom': ('ForestWatch Day4 Dice Zoom', 'E1 with per-image soft-F1 loss, zoomed crops and the sweep',
                       {'FW_DICE_WEIGHT': '1', 'FW_ZOOM_MIN': '256', 'FW_TTA_SWEEP': '1'}, []),
    'day4-e2': ('ForestWatch Day4 E2', 'E2: observed background, two-sided gate, delta fusion',
                {'FW_RUNS': 'E2', 'FW_FUSION': 'delta', 'FW_GATE_LOSS': 'bce', 'FW_INIT_BIAS': '0'}, []),
    'day4-e3': ('ForestWatch Day4 E3 Concat', 'baseline E3: reference image as extra input channels', {'FW_RUNS': 'E3'}, []),
    'day4-e4': ('ForestWatch Day4 E4 Diff', 'baseline E4: |image - reference| as extra input channels', {'FW_RUNS': 'E4'}, []),
}

LAUNCHER = r'''# __TITLE__ (__DESC__): smoke pass, then the full run.  Results: /kaggle/working/__TAG__/
import json, os, subprocess, sys, time
from pathlib import Path
W = Path('/kaggle/working')
RUN_ENV = __RUN_ENV__
SHOW = ('ENV', 'DATA', 'RUN_CONFIG', 'INIT', 'TARGET_UPDATES', 'VAL', 'TRAIN_DONE', 'RESULT', 'BOOTSTRAP', 'STOP_EARLY', 'ALL_DONE')


def run(tag, extra):
    env = {**os.environ, **RUN_ENV, **extra, 'FW_OUT': str(W / tag), 'PYTHONUNBUFFERED': '1'}
    t0 = time.time()
    with open(W / f'{tag}.log', 'w') as log, subprocess.Popen([sys.executable, '-u', str(W / 'fw_train.py')], env=env,
                                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                             text=True, bufsize=1) as p:
        for line in p.stdout:
            log.write(line); log.flush()
            head = line.split(' ', 1)[0]
            if head == 'TRAIN':  # every 500 updates
                try:
                    u = json.loads(line.split(' ', 1)[1])['update']
                except Exception:
                    u = 0
                show = u == 1 or u % 500 == 0
            else:
                show = head.startswith(SHOW) or 'Error' in line or 'Traceback' in line
            if show:
                print(line[:600], end='', flush=True)
        code = p.wait()
    print(f'== {tag} exit {code} after {(time.time() - t0) / 60:.1f} min', flush=True)
    assert code == 0, f'{tag} failed, see {W / tag}.log'


__SMOKE__run('__TAG__', {})
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--preset', choices=sorted(PRESETS))
    ap.add_argument('--title', help='Kaggle title; the notebook slug is derived from it')
    ap.add_argument('--env', action='append', default=[], metavar='KEY=VALUE', help='FW_* setting (repeatable)')
    ap.add_argument('--source', action='append', default=[], metavar='USER/KERNEL', help='extra kernel output to attach')
    ap.add_argument('--dataset', action='append', default=[], metavar='USER/DATASET', help='dataset to attach')
    ap.add_argument('--no-smoke', action='store_true', help='skip the 20-update smoke pass')
    ap.add_argument('--out', required=True, help='folder to write kernel-metadata.json and the notebook into')
    a = ap.parse_args()
    title, desc, env, extra_sources = PRESETS[a.preset] if a.preset else (a.title, 'custom run', {}, [])
    title = a.title or title
    if not title:
        ap.error('give --preset or --title')
    env = {**BASE, **env}
    for kv in a.env:
        k, sep, v = kv.partition('=')
        if not sep or not k.startswith('FW_'):
            ap.error(f'--env expects FW_KEY=VALUE, got {kv!r}')
        env[k] = v
    slug = '-'.join(title.lower().split())
    tag = slug.removeprefix('forestwatch-').replace('-', '_')
    smoke = '' if a.no_smoke else f"run('smoke_{tag}', dict(FW_LIMIT='8', FW_UPDATES='20', FW_N_VAL_EVALS='1'))\n"
    launcher = (LAUNCHER.replace('__TITLE__', title).replace('__DESC__', desc).replace('__TAG__', tag)
                .replace('__RUN_ENV__', json.dumps(env)).replace('__SMOKE__', smoke))
    compile(launcher, 'launcher', 'exec')
    cells = ['%%writefile /kaggle/working/fw_train.py\n' + build_source(), launcher]
    nb = {'cells': [{'cell_type': 'code', 'execution_count': None, 'metadata': {}, 'outputs': [], 'source': c} for c in cells],
          'metadata': {'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
                       'language_info': {'name': 'python'}},
          'nbformat': 4, 'nbformat_minor': 5}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{slug}.ipynb').write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding='utf-8')
    meta = dict(id=f'{USER}/{slug}', title=title, code_file=f'{slug}.ipynb', language='python', kernel_type='notebook',
                is_private=True, enable_gpu=True, enable_tpu=False, enable_internet=True, keywords=[],
                dataset_sources=a.dataset, kernel_sources=SOURCES + extra_sources + a.source, competition_sources=[], model_sources=[],
                machine_shape='NvidiaTeslaT4')
    (out / 'kernel-metadata.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(f'built {out} ({slug}); env {env}')


if __name__ == '__main__':
    main()
