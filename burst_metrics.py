"""Per-burst metrics for every mass burst in data/mass_bursts.csv.

Burst accounts = accounts with in_mass_burst in data/account_tiers.csv. For each burst, the burst is re-found with
the same rule as tiers.py (>= --burst-votes votes from burst accounts within --burst-min minutes, maximal run) on
the burst accounts' votes for that paper, so the counts match mass_bursts.csv:
  burst_votes          votes in the burst
  burst_start/end_utc  first and last vote of the burst, to the second
  seconds_all          time from first to last burst vote
  max_votes_60s        most burst-account votes inside any 60-second window
  share_of_upvotes     burst votes / the paper's total upvotes
  minutes_after_submission  burst start minus the paper's submission to Daily Papers (submitter's 'paper-daily' event)

For fast bursts (all except the synchronized Oct 19 push), every voter's timestamp is fetched and compared against
the paper's other voters (everyone who is not a burst account):
  other_voters_timed / other_voters   coverage
  other_max_votes_60s                 most votes from other voters inside any 60-second window, anywhere in time
  other_max_votes_3600s               same for any one-hour window
"""
import argparse, csv, json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from activity import daily_submit_time, paper_upvotes, paper_vote_time

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--skip-day", default="2026-10-19", help="burst date treated as the synchronized push (no full fetch)")
ap.add_argument("--burst-votes", type=int, default=50)
ap.add_argument("--burst-min", type=float, default=10)
args = ap.parse_args()


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fmt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def rows(name):
    return list(csv.DictReader(open(D / name, encoding="utf-8")))


def max_window(times, sec):
    times = sorted(times)
    best, j = 0, 0
    for i in range(len(times)):
        while times[i] - times[j] > sec:
            j += 1
        best = max(best, i - j + 1)
    return best


def runs(times):
    """Bursts with the tiers.py rule: >= burst_votes votes within burst_min of the run's first vote."""
    times, out, i, w = sorted(times), [], 0, args.burst_min * 60
    while i < len(times):
        j = i
        while j + 1 < len(times) and times[j + 1] - times[i] <= w:
            j += 1
        if j - i + 1 >= args.burst_votes:
            out.append(times[i:j + 1])
            i = j + 1
        else:
            i += 1
    return out


burst_accounts = {r["user"] for r in rows("account_tiers.csv") if r["in_mass_burst"] == "True"}
submitter = {}
for f in (D / "raw" / "daily").glob("*.json"):
    for it in json.loads(f.read_text(encoding="utf-8")) or []:
        if it["paper"].get("submittedOnDailyBy"):
            submitter.setdefault(it["paper"]["id"], it["paper"]["submittedOnDailyBy"]["user"])

out = []
for b in rows("mass_bursts.csv"):
    p = b["paper"]
    f = D / "raw" / "upvoters" / f"{p}.json"
    voters = [u["user"] for u in json.loads(f.read_text(encoding="utf-8"))] if f.exists() else []
    times = [ts(t) for u in burst_accounts for q, t in (paper_upvotes(u) or []) if q == p]
    start = ts(b["start_utc"].replace(" ", "T") + ":00Z")
    win = min(runs(times), key=lambda r: abs(r[0] - start))
    sub = daily_submit_time(p, submitter[p]) if p in submitter else None
    other = {}
    if voters and not b["start_utc"].startswith(args.skip_day):
        others = [u for u in voters if u not in burst_accounts]
        with ThreadPoolExecutor(6) as ex:
            ot = [ts(t) for t in ex.map(lambda u: paper_vote_time(u, p), others) if t]
        other = {"other_voters": len(others), "other_voters_timed": len(ot),
                 "other_max_votes_60s": max_window(ot, 60), "other_max_votes_3600s": max_window(ot, 3600)}
    out.append({
        "paper": p, "burst_start_utc": fmt(win[0]), "burst_end_utc": fmt(win[-1]), "burst_votes": len(win),
        "seconds_all": round(win[-1] - win[0], 1), "max_votes_60s": max_window(win, 60),
        "paper_upvotes": len(voters) or b["paper_upvotes"],
        "share_of_upvotes": round(len(win) / len(voters), 3) if voters else "",
        "minutes_after_submission": round((win[0] - ts(sub)) / 60, 1) if sub else "",
        "other_voters": other.get("other_voters", ""), "other_voters_timed": other.get("other_voters_timed", ""),
        "other_max_votes_60s": other.get("other_max_votes_60s", ""),
        "other_max_votes_3600s": other.get("other_max_votes_3600s", ""),
    })
out.sort(key=lambda r: -r["burst_votes"])
with open(D / "burst_metrics.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
for r in out:
    print(f"{r['paper']} {r['burst_start_utc']} votes={r['burst_votes']:>3} all={r['seconds_all']:>6}s "
          f"max/60s={r['max_votes_60s']:>3} share={r['share_of_upvotes']} after-submission={r['minutes_after_submission']} min"
          f" | other voters timed {r['other_voters_timed']}/{r['other_voters']}, peak/60s {r['other_max_votes_60s']}, "
          f"peak/h {r['other_max_votes_3600s']}")
