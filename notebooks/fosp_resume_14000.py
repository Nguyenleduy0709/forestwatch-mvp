"""Resume the saved 10k FoSp checkpoint, stop at 14k, and evaluate its best model."""
from pathlib import Path
import gc
import json
import os
import shutil
import subprocess
import sys
import zipfile

import torch

working = Path("/kaggle/working")
out = working / "fosp_smoke5k_14000"
sources = list(Path("/kaggle/input").rglob("fosp_smoke5k_10000/last.pth"))
assert len(sources) == 1, sources
source = sources[0].parent
snapshot = source.parent
assert torch.cuda.is_available(), "Enable a Kaggle GPU."
out.mkdir(exist_ok=True)
for name in ("best.pth", "manifest.json", "data_audit.json", "validation_history.json", "last.pth"):
    target = out / name
    if not target.exists():
        temporary = target.with_suffix(".copying")
        shutil.copy2(source / name, temporary)
        temporary.replace(target)
checkpoint = torch.load(out / "last.pth", map_location="cpu", weights_only=True)
start = checkpoint["update"]
assert 10000 <= start <= 14000 and "optimizer" in checkpoint and "rng" in checkpoint
print("RESUMING_14K", start, torch.cuda.get_device_name(0), flush=True)
del checkpoint
gc.collect()

repo = snapshot / "FoSp-official"
assert (repo / "config/fosp_config.py").is_file()
mmcv = Path("/kaggle/temp/mmcv")
if not (mmcv / "mmcv").is_dir():
    subprocess.run(["git", "clone", "--depth", "1", "--branch", "v1.6.2",
                    "https://github.com/open-mmlab/mmcv.git", str(mmcv)], check=True)
deps = Path("/kaggle/temp/fosp-deps")
if not all((deps / name).is_dir() for name in ("yapf", "gdown", "pkg_resources", "timm")):
    subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(deps), "--no-deps",
                    "setuptools==80.9.0", "wheel", "addict", "yapf==0.40.1", "easydict",
                    "future", "ninja", "timm", "gdown", "platformdirs", "tomli"], check=True)
env = os.environ.copy()
env["PYTHONPATH"] = os.pathsep.join(
    [str(repo), str(mmcv), str(deps)] +
    [path for path in sys.path if path and ("site-packages" in path or "dist-packages" in path)]
)
data_parent = working / "SMOKE5K-official"
if not (data_parent / "SMOKE5K/train/img").is_dir():
    archive_path = Path("/kaggle/temp/SMOKE5K-official.zip")
    if not zipfile.is_zipfile(archive_path):
        subprocess.run([sys.executable, "-c",
                        "import gdown,sys; assert gdown.download(id='11TM8hsh9R6ZTvLAUzfD6eD051MbOufCi', output=sys.argv[1])",
                        str(archive_path)], cwd=repo, env=env, check=True)
    data_parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        assert all((data_parent / item.filename).resolve().is_relative_to(data_parent.resolve())
                   and (item.external_attr >> 16) & 0o170000 != 0o120000
                   for item in archive.infolist())
        archive.extractall(data_parent)
manifest = json.loads((out / "manifest.json").read_text())
assert all(Path(row[key]).is_file() for rows in (manifest["train"], manifest["val"], manifest["test"])
           for row in rows for key in ("image", "mask"))
code = (snapshot / "train_smoke5k_10000.py").read_text()
assert code.count("min(args.max_updates, 10000)") == 2
assert code.count("if update == 10000 else") == 1
code = code.replace("min(args.max_updates, 10000)", "min(args.max_updates, 14000)")
code = code.replace("if update == 10000 else", "if update == 14000 else")
script = working / "train_smoke5k_14000.py"
compile(code, str(script), "exec")
script.write_text(code)

commands = []
if start < 14000:
    commands.append([sys.executable, "-u", str(script), "--output", str(out), "--resume",
                     "--max-updates", "40000", "--micro-batch", "2", "--accumulate", "3",
                     "--time-budget", "18000", "--eval-every", "1000"])
commands.append([sys.executable, "-u", str(script), "--output", str(out), "--evaluate-test"])
for command in commands:
    with subprocess.Popen(command, cwd=repo, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
        for line in process.stdout:
            print(line, end="", flush=True)
        assert process.wait() == 0, "FoSp subprocess failed. Read the preceding traceback."

last = torch.load(out / "last.pth", map_location="cpu", weights_only=True)
saved_update = last["update"]
assert saved_update == 14000
del last
best = torch.load(out / "best.pth", map_location="cpu", weights_only=True)
scores = json.loads((out / "test_metrics.json").read_text())
assert scores["n_images"] == 400
result = {"saved_update": saved_update, "best_update": best["update"],
          "selection_metric": "validation image_mean.f1", "validation": best["validation"],
          "test": scores, "test_used_for_selection": False}
(out / "results_14000.json").write_text(json.dumps(result, indent=2))
del best
gc.collect()
with zipfile.ZipFile(working / "forestwatch_fosp_14000.zip", "w", zipfile.ZIP_STORED) as bundle:
    for path in sorted(out.iterdir()):
        if path.is_file() and path.suffix != ".tmp":
            bundle.write(path, arcname=path.name)
print("SMOKE5K_14000_FINAL", json.dumps(result), flush=True)
print("DOWNLOAD_PACKAGE", str(working / "forestwatch_fosp_14000.zip"), flush=True)
