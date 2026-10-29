"""Check ring membership account by account, against a control group of ordinary new accounts.

Groups:
  seed            upvoters of the target paper created on/after --since (ring_accounts.csv group=seed)
  expanded_burst  expanded accounts created inside the seed's creation bursts (the 400 added to the core ring)
  expanded_other  expanded accounts created outside those bursts (excluded from the core ring)
  control         random sample of other accounts created on/after --since, default avatar, that upvoted
                  a scanned paper but are not in seed/expanded

Run after `investigate.py --profile-expanded` and `analyze.py`. Uses the same cache as investigate.py.
"""
import argparse, csv, json, random, re, urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from investigate import API, get, created_at, default_avatar
from analyze import batch

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"
RANDOM_MODEL = re.compile(r"^[a-z]+-[a-z]+-[0-9a-f]{6}$")
MODEL_HOUR = "2026-09-11T04"

ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2026-07-01")
ap.add_argument("--control-n", type=int, default=60)
args = ap.parse_args()
since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

ring = {r["user"]: r for r in csv.DictReader(open(D / "ring_accounts.csv", encoding="utf-8"))}

# every upvoter seen in the scan
users, votes = {}, defaultdict(int)
for f in (D / "raw" / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        users[u["user"]] = u
        votes[u["user"]] += 1

pool = sorted(n for n, u in users.items()
              if n not in ring and created_at(u["_id"]) >= since and default_avatar(u))
control = random.Random(0).sample(pool, min(args.control_n, len(pool)))


def group(n):
    if n in ring:
        r = ring[n]
        if r["group"] == "seed":
            return "seed"
        return "expanded_burst" if batch(r["created"][:10]) != "other" else "expanded_other"
    return "control"


seed_times = sorted(created_at(users[n]["_id"]).timestamp() for n, r in ring.items() if r["group"] == "seed")


def minutes_to_seed(n):
    """Minutes between this account's creation and the nearest *other* seed account's creation."""
    t = created_at(users[n]["_id"]).timestamp()
    return round(min(abs(t - s) for s in seed_times if s != t) / 60, 1)


def check(n):
    o = get(f"{API}/users/{n}/overview", f"users/{n}.json") or {}
    models = get(f"{API}/models?author={urllib.parse.quote(n)}&full=true", f"models/{n}.json") or []
    u = users[n]
    empty = [m for m in models if {s["rfilename"] for s in m.get("siblings", [])} <= {".gitattributes"}]
    return {
        "user": n,
        "group": group(n),
        "created": created_at(u["_id"]).strftime("%Y-%m-%d %H:%M"),
        "in_burst": batch(created_at(u["_id"]).strftime("%Y-%m-%d")) != "other",
        "minutes_to_seed": minutes_to_seed(n),
        "default_avatar": default_avatar(u),
        "has_org": bool(o.get("orgs")),
        "has_papers": o.get("numPapers", 0) > 0,
        "has_spaces": o.get("numSpaces", 0) > 0,
        "models": len(models),
        "empty_models": len(empty),
        "random_name_models": sum(1 for m in empty if RANDOM_MODEL.match(m["id"].split("/")[-1])),
        "model_in_sep11_04h": any(m.get("createdAt", "").startswith(MODEL_HOUR) for m in models),
        "likes": o.get("numLikes", 0),
        "upvotes": o.get("numUpvotes", 0),
        "followers": o.get("numFollowers", 0),
        "following": o.get("numFollowing", 0),
        "scanned_papers_voted": votes[n],
    }


with ThreadPoolExecutor(6) as ex:
    rows = list(ex.map(check, list(ring) + control))

# Evidence tiers, using only signals independent of how the account was selected (voting):
#   1 = created within 10 min of a seed account AND owns a random-name empty model
#   2 = timing only, 3 = model only, 4 = neither (unconfirmed; excluded from "confirmed")
for r in rows:
    t, m = r["minutes_to_seed"] <= 10, r["random_name_models"] > 0
    r["tier"] = 1 if t and m else 2 if t else 3 if m else 4
    r["confirmed"] = r["group"] in ("seed", "expanded_burst") and r["tier"] <= 3

with open(D / "verification.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

GROUPS = ["seed", "expanded_burst", "expanded_other", "control"]
summary = []
for g in GROUPS:
    rs = [r for r in rows if r["group"] == g]
    if not rs:
        continue
    pct = lambda k: f"{100 * sum(1 for r in rs if r[k]) / len(rs):.0f}%"
    summary.append({
        "group": g, "accounts": len(rs),
        "created_in_burst": pct("in_burst"),
        "within_10min_of_seed": f"{100 * sum(1 for r in rs if r['minutes_to_seed'] <= 10) / len(rs):.0f}%",
        "median_min_to_seed": median(r["minutes_to_seed"] for r in rs),
        "has_org": pct("has_org"), "has_papers": pct("has_papers"), "has_spaces": pct("has_spaces"),
        "has_any_model": f"{100 * sum(1 for r in rs if r['models']) / len(rs):.0f}%",
        "all_models_empty": f"{100 * sum(1 for r in rs if r['models'] and r['empty_models'] == r['models']) / max(1, sum(1 for r in rs if r['models'])):.0f}% of those",
        "model_in_sep11_04h": pct("model_in_sep11_04h"),
        "random_name_model": f"{100 * sum(1 for r in rs if r['random_name_models']) / len(rs):.0f}%",
        "median_likes": median(r["likes"] for r in rs),
        "median_upvotes": median(r["upvotes"] for r in rs),
        "median_following": median(r["following"] for r in rs),
        "median_scanned_papers_voted": median(r["scanned_papers_voted"] for r in rs),
    })
with open(D / "verification_summary.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(summary[0].keys())); w.writeheader(); w.writerows(summary)

keys = list(summary[0].keys())
print("\t".join(keys))
for s in summary:
    print("\t".join(str(s[k]) for k in keys))

# per-paper votes from confirmed accounts only
confirmed = {r["user"] for r in rows if r["confirmed"]}
papers = {r["id"]: r for r in csv.DictReader(open(D / "papers_scored.csv", encoding="utf-8"))}
prow = []
for pid, p in papers.items():
    us = json.loads((D / "raw" / "upvoters" / f"{pid}.json").read_text(encoding="utf-8")) or []
    c = sum(1 for u in us if u["user"] in confirmed)
    prow.append({k: p[k] for k in ("date", "id", "title", "upvotes", "submitted_by", "org")} |
                {"confirmed_ring_votes": c, "confirmed_ring_frac": round(c / max(len(us), 1), 3),
                 "upvotes_without_ring": int(p["upvotes"]) - c})
prow.sort(key=lambda r: -r["confirmed_ring_votes"])
with open(D / "papers_confirmed_ring.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(prow[0].keys())); w.writeheader(); w.writerows(prow)
print(f"\nconfirmed ring accounts: {len(confirmed)}  tiers: "
      f"{ {t: sum(1 for r in rows if r['confirmed'] and r['tier'] == t) for t in (1, 2, 3)} }")
