import ast, glob, os
from pathlib import Path
import numpy as np, cv2
from PIL import Image
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
src = (HERE.parent / 'prep_temporal.py').read_text()
tree = ast.parse(src)
ns = {'np': np, 'Image': Image, 'SIZE': (512, 512)}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in ('psnr', 'register', 'warp_to_seg'):
        exec(compile(ast.Module([node], []), 'prep', 'exec'), ns)
rng = np.random.default_rng(0)
FOSP = Path(os.environ.get('FOSP_DIR', REPO / '.fosp-runtime' / 'FoSp'))
files = sorted(glob.glob(str(FOSP / 'data/SmokeSeg/JPEGImages/*.jpg')))[:6]
assert files, 'set FOSP_DIR to a FoSp checkout (it ships 40 sample SmokeSeg images)'
for k, f in enumerate(files):
    seg = np.asarray(Image.open(f).convert('RGB'))
    H, W = (2048, 3072) if k % 2 else (1536, 2048)
    # a smooth random "rest of the scene" + the crop region placed at a known box
    canvas = cv2.resize((rng.random((H // 64, W // 64, 3)) * 255).astype(np.uint8), (W, H), interpolation=cv2.INTER_CUBIC)
    bw, bh = (int(rng.uniform(900, W - 10)), None)
    bh = int(bw * rng.uniform(0.6, 1.0)) if k % 3 else bw
    bh = min(bh, H - 10)
    x0, y0 = int(rng.uniform(0, W - bw)), int(rng.uniform(0, H - bh))
    canvas[y0:y0 + bh, x0:x0 + bw] = cv2.resize(seg, (bw, bh), interpolation=cv2.INTER_CUBIC)
    M, info = ns['register'](seg, canvas)
    true = np.array([[bw / 512, 0, x0 + 0.5 * bw / 512 - 0.5], [0, bh / 512, y0 + 0.5 * bh / 512 - 0.5]])
    err = np.abs(M - true).max() if M is not None else None
    print(f'img{k} box=({x0},{y0},{bw},{bh}) ok={M is not None} info={info} max|M-true|={err:.3f}' if M is not None else f'img{k} FAILED {info}')
