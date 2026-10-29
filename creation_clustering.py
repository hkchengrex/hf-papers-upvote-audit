"""Were the accounts that voted in the fast bursts created at about the same time? No network requests.

Selection uses vote time only, never creation time or any flag: every upvoter of a fast-burst paper (burst with
>= --min-peak votes in one minute, data/burst_metrics.csv) whose vote falls inside the burst window is included.

Their creation times are compared with random draws of the same size from all new accounts (created on/after
--since) seen in the scan, flagged or not. Including flagged accounts in the pool can only make the pool look more
clustered, so the comparison is conservative.

Statistics (higher = more "created together"):
  median_nearby  median number of others from the same set created within +-10 min
  dense_share    share of accounts with >= 5 others from the same set created within +-10 min
  hours_80       fewest distinct clock hours that together hold 80% of the accounts

Writes data/burst_window_voters.csv (one row per paper and voter) and data/creation_clustering.csv.
"""
import argparse, csv, json, random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from activity import paper_vote_time
from investigate import created_at

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2026-07-01")
ap.add_argument("--min-peak", type=int, default=50, help="fast burst: at least this many votes in one minute")
ap.add_argument("--draws", type=int, default=200)
args = ap.parse_args()
since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T") + ("" if "+" in s or "Z" in s else "+00:00")).timestamp()


users = {}
for f in (D / "raw" / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        users[u["user"]] = u
ctime = lambda u: created_at(users[u]["_id"]).timestamp()

selected, picked = set(), []
for b in csv.DictReader(open(D / "burst_metrics.csv", encoding="utf-8")):
    if int(b["max_votes_60s"]) < args.min_peak:
        continue
    p, t0, t1 = b["paper"], ts(b["burst_start_utc"]), ts(b["burst_end_utc"])
    voters = [u["user"] for u in json.loads((D / "raw" / "upvoters" / f"{p}.json").read_text(encoding="utf-8"))]
    inside = [u for u in voters if (t := paper_vote_time(u, p)) and t0 <= ts(t) <= t1]
    print(f"{p}: {len(inside)} voters inside {b['burst_start_utc']} .. {b['burst_end_utc']}")
    selected.update(inside)
    picked += [{"paper": p, "user": u, "voted_utc": paper_vote_time(u, p),
                "created_utc": created_at(users[u]["_id"]).strftime("%Y-%m-%d %H:%M:%S")} for u in inside]

new_selected = [u for u in selected if created_at(users[u]["_id"]) >= since]
pool = [u for u, x in users.items() if created_at(x["_id"]) >= since]


def nearby(ts_, window=600):
    ts_ = sorted(ts_)
    n, lo, hi, out = len(ts_), 0, 0, []
    for i in range(n):
        while ts_[i] - ts_[lo] > window:
            lo += 1
        while hi + 1 < n and ts_[hi + 1] - ts_[i] <= window:
            hi += 1
        out.append(hi - lo)
    return out


def cover(ts_, unit):
    counts = sorted(Counter(int(t // unit) for t in ts_).values(), reverse=True)
    total = k = 0
    while total < 0.8 * len(ts_):
        total += counts[k]
        k += 1
    return k


def stats(us):
    t = [ctime(u) for u in us]
    nb = nearby(t)
    return {"median_nearby": median(nb), "dense_share": sum(x >= 5 for x in nb) / len(nb), "hours_80": cover(t, 3600)}


real = stats(new_selected)
sims = [stats(random.Random(s).sample(pool, len(new_selected))) for s in range(args.draws)]
out = [{"set": "burst-window voters (new accounts)", "n": len(new_selected), **{k: round(v, 3) for k, v in real.items()},
        "range_over_draws": ""},
       {"set": f"random new accounts (pool {len(pool)}, {args.draws} draws, median)", "n": len(new_selected),
        **{k: round(median(x[k] for x in sims), 3) for k in real},
        "range_over_draws": f"median_nearby max {max(x['median_nearby'] for x in sims)}, "
                            f"dense_share max {max(x['dense_share'] for x in sims):.3f}, "
                            f"hours_80 min {min(x['hours_80'] for x in sims)}"}]
with open(D / "burst_window_voters.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(picked[0].keys())); w.writeheader(); w.writerows(picked)
with open(D / "creation_clustering.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
print(f"selected by vote time: {len(selected)} accounts, {len(new_selected)} created on/after {args.since}")
for r in out:
    print(f"  {r['set']}: n={r['n']} median nearby {r['median_nearby']}, >=5 nearby {r['dense_share']:.1%}, "
          f"80% in {r['hours_80']} hours {r['range_over_draws']}")
