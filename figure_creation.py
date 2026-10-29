"""PNG figure: how many accounts from the same set were created within +-10 minutes of each account.

Burst-window voters = new accounts (created on/after --since) that voted inside a fast-burst window, selected by
vote time only (data/burst_window_voters.csv, from creation_clustering.py). Random = the same number of new
accounts drawn from everyone seen in the scan, flagged or not; the histogram averages --draws draws.

Accounts are not named in the figure. No third-party Python dependencies: the SVG is drawn here and rasterized
with headless Microsoft Edge (--edge to point at another Chromium-based browser).
"""
import argparse, csv, json, random, subprocess, tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from investigate import created_at

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2026-07-01")
ap.add_argument("--window", type=int, default=10, help="minutes either side")
ap.add_argument("--cap", type=int, default=20, help="last bin is cap+")
ap.add_argument("--draws", type=int, default=200)
ap.add_argument("--out", default="figures/creation.png")
ap.add_argument("--scale", type=int, default=2, help="pixel density of the PNG")
ap.add_argument("--edge", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
args = ap.parse_args()
since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

users = {}
for f in (D / "raw" / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        users[u["user"]] = u
ctime = lambda u: created_at(users[u]["_id"]).timestamp()
selected = sorted({r["user"] for r in csv.DictReader(open(D / "burst_window_voters.csv", encoding="utf-8"))})
selected = [u for u in selected if created_at(users[u]["_id"]) >= since]
pool = [u for u, x in users.items() if created_at(x["_id"]) >= since]


def neighbours(ts):
    """For each account, how many others in the same set were created within +-window minutes."""
    ts = sorted(ts)
    n, lo, hi, out = len(ts), 0, 0, []
    w = args.window * 60
    for i in range(n):
        while ts[i] - ts[lo] > w:
            lo += 1
        while hi + 1 < n and ts[hi + 1] - ts[i] <= w:
            hi += 1
        out.append(hi - lo)
    return out


def summarize(v):
    return {"n": len(v), "median": median(v), "zero": sum(x == 0 for x in v) / len(v),
            "ge5": sum(x >= 5 for x in v) / len(v),
            "hist": [sum(1 for x in v if min(x, args.cap) == b) / len(v) for b in range(args.cap + 1)]}


stats = {"flagged": summarize(neighbours([ctime(u) for u in selected]))}
draws = [summarize(neighbours([ctime(u) for u in random.Random(s).sample(pool, len(selected))])) for s in range(args.draws)]
stats["genuine"] = {"n": len(selected), "median": median(d["median"] for d in draws),
                    "zero": sum(d["zero"] for d in draws) / len(draws), "ge5": sum(d["ge5"] for d in draws) / len(draws),
                    "hist": [sum(d["hist"][b] for d in draws) / len(draws) for b in range(args.cap + 1)]}
for k, v in stats.items():
    print(f"{k}: n={v['n']} median {v['median']} | none {v['zero']:.1%} | >=5 {v['ge5']:.1%}")

# ---- SVG ----
W, HT = 620, 400
P = {"l": 64, "r": 16, "t": 72, "b": 58}
C = {"flagged": "#eb6834", "genuine": "#2a78d6", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
     "grid": "#e1e0d9", "axis": "#c3c2b7", "bg": "#ffffff"}
NAME = {"flagged": f"New accounts that voted inside a burst window (n = {stats['flagged']['n']})",
        "genuine": f"Random new accounts, same number (average of {args.draws} draws)"}
font = 'font-family="system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif"'
out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {HT}" width="{W}" height="{HT}" {font}>',
       f'<rect width="{W}" height="{HT}" fill="{C["bg"]}"/>']


def text(x, y, s, size=12, fill=None, anchor="start", weight=None, extra=""):
    w = f' font-weight="{weight}"' if weight else ""
    out.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" fill="{fill or C["muted"]}"{w} {extra}>{s}</text>')


pw, ph = W - P["l"] - P["r"], HT - P["t"] - P["b"]
bins = list(range(args.cap + 1))
vmax = max(max(stats[k]["hist"]) for k in ("flagged", "genuine")) * 100
step = next(s for s in (5, 10, 20, 25) if vmax / s <= 6)
ymax = -(-vmax // step) * step
y = lambda v: P["t"] + ph - ph * v / ymax
for v in range(0, int(ymax) + 1, step):
    out.append(f'<line x1="{P["l"]}" x2="{P["l"] + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{C["grid"] if v else C["axis"]}"/>')
    text(P["l"] - 8, y(v) + 4, f"{v}%", anchor="end")
gw = pw / len(bins)
bw = (gw - 6) / 2
for i, b in enumerate(bins):
    for j, k in enumerate(("genuine", "flagged")):
        v = stats[k]["hist"][b] * 100
        if v <= 0:
            continue
        x = P["l"] + gw * i + 3 + j * bw
        r = min(3, bw / 2, y(0) - y(v))
        out.append(f'<path d="M{x + 1:.1f},{y(0):.1f}V{y(v) + r:.1f}Q{x + 1:.1f},{y(v):.1f} {x + 1 + r:.1f},{y(v):.1f}'
                   f'H{x + bw - 1 - r:.1f}Q{x + bw - 1:.1f},{y(v):.1f} {x + bw - 1:.1f},{y(v) + r:.1f}V{y(0):.1f}Z" fill="{C[k]}"/>')
    if b % 2 == 0 or b == args.cap:
        text(P["l"] + gw * i + gw / 2, P["t"] + ph + 19, f"{b}+" if b == args.cap else b, anchor="middle")
text(P["l"] + pw / 2, HT - 16, f"Other accounts from the same set created within ±{args.window} minutes", 13, C["ink2"], "middle")
out.append(f'<text transform="translate(20 {P["t"] + ph / 2}) rotate(-90)" text-anchor="middle" font-size="13" '
           f'fill="{C["ink2"]}">Share of accounts</text>')

# legend, one entry per row
for i, k in enumerate(("flagged", "genuine")):
    ly = 22 + i * 22
    out.append(f'<rect x="{P["l"]}" y="{ly - 10}" width="12" height="12" rx="2" fill="{C[k]}"/>')
    text(P["l"] + 18, ly, NAME[k], 13, C["ink2"])
out.append("</svg>")

png = ROOT / args.out
png.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    svg = Path(tmp) / "creation.svg"
    svg.write_text("\n".join(out), encoding="utf-8")
    subprocess.run([args.edge, "--headless", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{HT}",
                    f"--force-device-scale-factor={args.scale}", f"--screenshot={png}", svg.as_uri()],
                   check=True, capture_output=True)
print(f"wrote {args.out} ({W * args.scale}x{HT * args.scale})")
