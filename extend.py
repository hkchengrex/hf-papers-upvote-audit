"""Second pass: find ring members the vote-based expansion missed, and examine the late Sep–Oct account spike.

Part A (missed members): new default-avatar upvoters, not already confirmed, created within --window minutes of a
confirmed ring account. Each is profiled and tiered with the same vote-independent signals as verify.py.

Part B (spike): new default-avatar upvoters created on --spike-days. Reports their creation-time clustering,
what they voted on, and a profiled random sample compared with the verify.py control group.

Run after verify.py. Uses the investigate.py cache.
"""
import argparse, bisect, csv, json, random, re, urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from investigate import API, get, created_at, default_avatar

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"
RANDOM_MODEL = re.compile(r"^[a-z]+-[a-z]+-[0-9a-f]{6}$")

ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2026-07-01")
ap.add_argument("--window", type=float, default=10, help="minutes to nearest confirmed ring account")
ap.add_argument("--spike-days", default="2026-09-26,2026-09-29,2026-09-30,2026-10-01,2026-10-16,2026-10-19")
ap.add_argument("--spike-sample", type=int, default=60)
args = ap.parse_args()
since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

ver = list(csv.DictReader(open(D / "verification.csv", encoding="utf-8")))
confirmed = {r["user"] for r in ver if r["confirmed"] == "True"}
control = {r["user"] for r in ver if r["group"] == "control"}

users, voted = {}, {}
for f in (D / "raw" / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        users[u["user"]] = u
        voted.setdefault(u["user"], set()).add(f.stem)
papers = {r["id"]: r for r in csv.DictReader(open(D / "papers_scored.csv", encoding="utf-8"))}
boosted = {r["id"] for r in csv.DictReader(open(D / "papers_confirmed_ring.csv", encoding="utf-8"))
           if int(r["confirmed_ring_votes"]) >= 20}

ring_t = sorted(created_at(users[n]["_id"]).timestamp() for n in confirmed)
other = [n for n, u in users.items()
         if n not in confirmed and created_at(u["_id"]) >= since and default_avatar(u)]


def minutes_to_ring(n):
    t = created_at(users[n]["_id"]).timestamp()
    i = bisect.bisect_left(ring_t, t)
    return min(abs(t - ring_t[j]) for j in (i - 1, i) if 0 <= j < len(ring_t)) / 60


def profile(n):
    o = get(f"{API}/users/{n}/overview", f"users/{n}.json") or {}
    models = get(f"{API}/models?author={urllib.parse.quote(n)}&full=true", f"models/{n}.json") or []
    empty = [m for m in models if {s["rfilename"] for s in m.get("siblings", [])} <= {".gitattributes"}]
    return {
        "user": n,
        "created": created_at(users[n]["_id"]).strftime("%Y-%m-%d %H:%M"),
        "minutes_to_ring": round(minutes_to_ring(n), 1),
        "has_org": bool(o.get("orgs")), "has_papers": o.get("numPapers", 0) > 0,
        "models": len(models), "empty_models": len(empty),
        "random_name_models": sum(1 for m in empty if RANDOM_MODEL.match(m["id"].split("/")[-1])),
        "model_in_sep11_04h": any(m.get("createdAt", "").startswith("2026-09-11T04") for m in models),
        "likes": o.get("numLikes", 0), "upvotes": o.get("numUpvotes", 0),
        "followers": o.get("numFollowers", 0), "following": o.get("numFollowing", 0),
        "scanned_papers_voted": len(voted[n]), "boosted_papers_voted": len(voted[n] & boosted),
    }


def summarize(name, rs):
    n = len(rs)
    share = lambda f: f"{100 * sum(1 for r in rs if f(r)) / n:.0f}%"
    return {
        "group": name, "accounts": n,
        "within_2min_of_ring": share(lambda r: r["minutes_to_ring"] <= 2),
        "has_any_model": share(lambda r: r["models"]),
        "random_name_model": share(lambda r: r["random_name_models"]),
        "has_org_or_paper": share(lambda r: r["has_org"] or r["has_papers"]),
        "voted_boosted_paper": share(lambda r: r["boosted_papers_voted"]),
        "median_likes": median(r["likes"] for r in rs), "median_upvotes": median(r["upvotes"] for r in rs),
        "median_following": median(r["following"] for r in rs),
        "median_scanned_papers_voted": median(r["scanned_papers_voted"] for r in rs),
    }


def write_csv(name, rows):
    with open(D / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


# ---- Part A: missed ring members ----
cand = sorted(n for n in other if minutes_to_ring(n) <= args.window)
with ThreadPoolExecutor(6) as ex:
    a_rows = list(ex.map(profile, cand))
for r in a_rows:
    t, m = r["minutes_to_ring"] <= 2, r["random_name_models"] > 0
    # stricter than verify.py: candidates were found by timing alone, so timing alone is not enough
    r["tier"] = 1 if t and m else 2 if m else 3 if t and r["boosted_papers_voted"] else 4
    r["added"] = r["tier"] <= 3
write_csv("missed_candidates.csv", a_rows)
print(f"Part A: {len(cand)} candidates within {args.window} min of a confirmed ring account")
print("  tiers:", dict(sorted(Counter(r["tier"] for r in a_rows).items())),
      "(1 = <=2 min + random-name empty model, 2 = model only, 3 = <=2 min + voted a boosted paper, 4 = not added)")
added = {r["user"] for r in a_rows if r["added"]}
print(f"  added to ring: {len(added)}")

# per-paper votes from the final ring (confirmed + added)
final = confirmed | added
frow = []
for pid, p in papers.items():
    us = json.loads((D / "raw" / "upvoters" / f"{pid}.json").read_text(encoding="utf-8")) or []
    c = sum(1 for u in us if u["user"] in final)
    frow.append({k: p[k] for k in ("date", "id", "title", "upvotes", "submitted_by", "org")} |
                {"ring_votes": c, "ring_frac": round(c / max(len(us), 1), 3), "upvotes_without_ring": int(p["upvotes"]) - c})
frow.sort(key=lambda r: -r["ring_votes"])
write_csv("papers_ring_final.csv", frow)
fb = [r for r in frow if r["ring_votes"] >= 20]
print(f"  final ring {len(final)} accounts; {len(fb)} papers with >=20 ring votes, "
      f"{sum(r['ring_votes'] for r in fb)}/{sum(int(r['upvotes']) for r in fb)} of their upvotes")

# ---- Part B: Sep–Oct spike ----
spike_days = set(args.spike_days.split(","))
spike = [n for n in other if n not in added and created_at(users[n]["_id"]).strftime("%Y-%m-%d") in spike_days]
print(f"\nPart B: {len(spike)} new default-avatar upvoters created on {sorted(spike_days)}")
for day in sorted(spike_days):
    ns = [n for n in spike if created_at(users[n]["_id"]).strftime("%Y-%m-%d") == day]
    pc = Counter(p for n in ns for p in voted[n])
    nv = Counter(len(voted[n]) for n in ns)
    top = ", ".join(f"{p} ({c}, {papers[p]['upvotes']} up)" for p, c in pc.most_common(3))
    print(f"  {day}: {len(ns)} accts | 1 paper: {nv.get(1, 0)} | 2+: {sum(v for k, v in nv.items() if k > 1)} | top: {top}")

# per-paper share of upvotes coming from spike-day accounts
spike_set = set(spike)
prow = []
for pid, p in papers.items():
    us = json.loads((D / "raw" / "upvoters" / f"{pid}.json").read_text(encoding="utf-8")) or []
    c = sum(1 for u in us if u["user"] in spike_set)
    if c:
        prow.append({"date": p["date"], "id": pid, "title": p["title"], "upvotes": int(p["upvotes"]),
                     "spike_votes": c, "spike_frac": round(c / max(len(us), 1), 3)})
prow.sort(key=lambda r: -r["spike_votes"])
write_csv("spike_papers.csv", prow)
print("  top papers by spike-account votes:")
for r in prow[:10]:
    print(f"    {r['date']} {r['id']} up={r['upvotes']} spike={r['spike_votes']} ({r['spike_frac']:.0%}) | {r['title'][:60]}")

sample = random.Random(0).sample(spike, min(args.spike_sample, len(spike)))
with ThreadPoolExecutor(6) as ex:
    b_rows = list(ex.map(profile, sample))
    c_rows = list(ex.map(profile, sorted(control)))
write_csv("spike_sample.csv", b_rows)
summary = [summarize("missed_added", [r for r in a_rows if r["added"]]),
           summarize("missed_not_added", [r for r in a_rows if not r["added"]]),
           summarize("spike_sample", b_rows),
           summarize("control", c_rows)]
write_csv("extend_summary.csv", summary)
print()
for s in summary:
    print(s)
