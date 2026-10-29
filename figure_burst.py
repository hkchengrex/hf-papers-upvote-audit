"""PNG figure: hourly upvote arrivals over the first --hours hours after each paper was submitted to Daily Papers,
anomalous vs other accounts, one panel per paper (stacked, independent y-scales).

Submission time = the submitter's 'paper-daily' activity event (exact to the second; the paper record itself only
gives the date). An arrow marks the bar holding each paper's largest burst, annotated with the
burst size and duration from data/burst_metrics.csv.

Anomalous = burst accounts (in_mass_burst in data/account_tiers.csv). Vote times come from activity feeds
(activity.paper_vote_time). Papers are not named in the figure. No third-party Python dependencies: the SVG is drawn
here and rasterized with headless Microsoft Edge (--edge to point at another Chromium-based browser).
"""
import argparse, csv, json, subprocess, tempfile
from datetime import datetime
from pathlib import Path

from activity import daily_submit_time, paper_vote_time

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--papers", default="2610.35201,2608.12564,2609.15818")
ap.add_argument("--hours", type=int, default=24)
ap.add_argument("--out", default="figures/burst.png")
ap.add_argument("--scale", type=int, default=4, help="pixel density of the PNG")
ap.add_argument("--edge", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
args = ap.parse_args()

anomalous = {r["user"] for r in csv.DictReader(open(D / "account_tiers.csv", encoding="utf-8")) if r["in_mass_burst"] == "True"}
metrics = {}
for r in csv.DictReader(open(D / "burst_metrics.csv", encoding="utf-8")):
    if r["paper"] not in metrics or int(r["burst_votes"]) > int(metrics[r["paper"]]["burst_votes"]):
        metrics[r["paper"]] = r


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fmt_dur(sec):
    n, unit = (round(sec), "second") if sec < 90 else (round(sec / 60), "minute")
    return f"{n} {unit}{'' if n == 1 else 's'}"


submitter = {}
for f in (D / "raw" / "daily").glob("*.json"):
    for it in json.loads(f.read_text(encoding="utf-8")) or []:
        if it["paper"].get("submittedOnDailyBy"):
            submitter.setdefault(it["paper"]["id"], it["paper"]["submittedOnDailyBy"]["user"])


def load(paper):
    voters = [u["user"] for u in json.loads((D / "raw" / "upvoters" / f"{paper}.json").read_text(encoding="utf-8"))]
    votes = sorted((ts(t), u in anomalous) for u in voters if (t := paper_vote_time(u, paper)))
    t0 = ts(daily_submit_time(paper, submitter[paper]))
    before = sum(1 for t, _ in votes if t < t0)
    if before:
        print(f"{paper}: {before} votes before submission are not shown")
    bins = [[0, 0] for _ in range(args.hours)]  # [other, anomalous]
    for t, a in votes:
        h = int((t - t0) // 3600)
        if 0 <= h < args.hours:
            bins[h][a] += 1
    m = metrics[paper]
    start = ts(m["burst_start_utc"].replace(" ", "T") + "Z")
    return {"bins": bins, "burst_bin": max(0, int((start - t0) // 3600)),
            "label": f"{m['burst_votes']} votes in {fmt_dur(float(m['seconds_all']))}"}


panels = [load(p) for p in args.papers.split(",")]
for p, P in zip(args.papers.split(","), panels):
    print(f"{p}: first {args.hours} h {sum(b[1] for b in P['bins'])} anomalous, {sum(b[0] for b in P['bins'])} other; "
          f"burst in hour {P['burst_bin']}: {P['label']}")

# ---- SVG ----
W, PH, GAP = 900, 260, 34
M = {"l": 64, "r": 24, "t": 56, "b": 60}
HT = M["t"] + len(panels) * PH + (len(panels) - 1) * GAP + M["b"]
iw, ih = W - M["l"] - M["r"], PH - 34
H = args.hours
bw = iw / H
x = lambda h: M["l"] + iw * h / H

C = {"other": "#2a78d6", "anom": "#eb6834", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
     "grid": "#e1e0d9", "axis": "#c3c2b7", "bg": "#ffffff"}
font = 'font-family="system-ui, -apple-system, Segoe UI, Helvetica, Arial, sans-serif"'
out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {HT}" width="{W}" height="{HT}" {font}>',
       f'<rect width="{W}" height="{HT}" fill="{C["bg"]}"/>',
       f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
       f'<path d="M0,0 L10,5 L0,10 z" fill="{C["ink"]}"/></marker></defs>']


def bar(x0, yt, w, h):
    return f"M{x0:.2f},{yt + h:.2f}V{yt:.2f}H{x0 + w:.2f}V{yt + h:.2f}Z" if h > 0 else ""


for n, P in enumerate(panels):
    top = M["t"] + n * (PH + GAP)
    base = top + 34 + ih
    ymax_raw = max(sum(b) for b in P["bins"])
    step = next(s for s in (5, 10, 20, 25, 50, 100, 200) if ymax_raw / s <= 5)
    ymax = -(-ymax_raw // step) * step
    y = lambda v: base - ih * v / ymax
    out.append(f'<text x="{M["l"] - 52}" y="{top + 16}" font-size="15" font-weight="600" fill="{C["ink"]}">'
               f'({chr(97 + n)})</text>')
    for v in range(0, ymax + 1, step):
        out.append(f'<line x1="{M["l"]}" x2="{W - M["r"]}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                   f'stroke="{C["grid"] if v else C["axis"]}" stroke-width="1"/>')
        out.append(f'<text x="{M["l"] - 8}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="12" fill="{C["muted"]}">{v}</text>')
    for h in range(0, H + 1, 6 if H > 24 else 4):
        out.append(f'<line x1="{x(h):.1f}" x2="{x(h):.1f}" y1="{base}" y2="{base + 5}" stroke="{C["axis"]}"/>')
        out.append(f'<text x="{x(h):.1f}" y="{base + 19}" text-anchor="middle" font-size="12" fill="{C["muted"]}">{h}</text>')
    # contiguous bins (a histogram, not a bar chart)
    for h, (o, a) in enumerate(P["bins"]):
        if o:
            out.append(f'<path d="{bar(x(h), y(o), bw, base - y(o))}" fill="{C["other"]}"/>')
        if a:
            out.append(f'<path d="{bar(x(h), y(o + a), bw, y(o) - y(o + a))}" fill="{C["anom"]}"/>')
    bb = P["burst_bin"]
    bx, by = x(bb) + bw, y(sum(P["bins"][bb])) + 12
    lx, ly = bx + 110, by - 2
    out.append(f'<path d="M{lx - 6:.1f},{ly:.1f} Q{bx + 40:.1f},{ly - 6:.1f} {bx + 4:.1f},{by:.1f}" fill="none" '
               f'stroke="{C["ink"]}" stroke-width="1.5" marker-end="url(#ah)"/>')
    out.append(f'<text x="{lx}" y="{ly + 5:.1f}" font-size="15" font-weight="600" fill="{C["ink"]}">{P["label"]}</text>')

mid = M["t"] + (HT - M["t"] - M["b"]) / 2
out.append(f'<text transform="translate(16 {mid}) rotate(-90)" text-anchor="middle" font-size="13" '
           f'fill="{C["ink2"]}">Upvotes per hour</text>')
out.append(f'<text x="{M["l"] + iw / 2}" y="{HT - 14}" text-anchor="middle" font-size="13" fill="{C["ink2"]}">'
           f'Hours since submission to Daily Papers</text>')
lgy = 28
for i, (key, name) in enumerate((("anom", "Anomalous accounts"), ("other", "Other accounts"))):
    ox = M["l"] + i * 190
    out.append(f'<rect x="{ox}" y="{lgy - 10}" width="12" height="12" rx="2" fill="{C[key]}"/>')
    out.append(f'<text x="{ox + 18}" y="{lgy}" font-size="13" fill="{C["ink2"]}">{name}</text>')
out.append(f'<text x="{W - M["r"]}" y="{lgy}" text-anchor="end" font-size="13" fill="{C["ink2"]}">First {H} hours after submission</text>')
out.append("</svg>")

png = ROOT / args.out
png.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    svg = Path(tmp) / "burst.svg"
    svg.write_text("\n".join(out), encoding="utf-8")
    subprocess.run([args.edge, "--headless", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{HT}",
                    f"--force-device-scale-factor={args.scale}", f"--screenshot={png}", svg.as_uri()],
                   check=True, capture_output=True)
print(f"wrote {args.out} ({W * args.scale}x{HT * args.scale})")
