"""Rerun the whole analysis offline from the shipped API snapshot (data/raw.tar.xz).

Extracts the snapshot to data/raw/ if needed, then runs every script in order with HF_OFFLINE=1 (a cache miss
returns None instead of fetching live data) and PYTHONHASHSEED=0 (fixed set ordering), so the outputs match the
CSVs in data/. --figures also redraws the PNGs, which needs Microsoft Edge or another Chromium-based browser.
"""
import argparse, os, subprocess, sys, tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = [
    ["investigate.py", "--days", "120", "--min-upvotes", "100", "--profile-expanded"],  # scan papers and upvoters
    ["analyze.py"], ["verify.py"], ["extend.py"],                                       # seed-based ring
    ["cohorts.py"], ["background.py"], ["tiers.py"],                                    # candidates and mass bursts
    ["burst_metrics.py"], ["fingerprint.py"], ["oct19_push.py"],                        # 1. burst voting, 3. repos
    ["creation_clustering.py"],                                                         # 2. account creation
]
FIGURES = [["figure_burst.py"], ["figure_creation.py"]]

ap = argparse.ArgumentParser()
ap.add_argument("--figures", action="store_true")
ap.add_argument("--online", action="store_true", help="fetch anything missing from the live API")
args = ap.parse_args()

raw = ROOT / "data" / "raw"
if not raw.exists():
    print("extracting data/raw.tar.xz")
    with tarfile.open(ROOT / "data" / "raw.tar.xz") as t:
        t.extractall(ROOT / "data")
env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8"}
if not args.online:
    env["HF_OFFLINE"] = "1"
for step in STEPS + (FIGURES if args.figures else []):
    print(f"== {' '.join(step)}", flush=True)
    subprocess.run([sys.executable, *step], cwd=ROOT, env=env, check=True)
