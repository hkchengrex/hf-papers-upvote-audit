"""Investigate suspicious upvote patterns on Hugging Face daily papers.

Stages (all HTTP responses are cached under data/raw, so reruns are cheap):
  1. Fetch daily paper listings for the last --days days.
  2. Fetch the upvoter list of every paper with >= --min-upvotes.
  3. Seed the ring: upvoters of --target whose accounts were created on/after --seed-since.
  4. Score every paper by how many seed-ring accounts upvoted it.
  5. Expand: find accounts outside the seed that look like ring members (young account,
     default avatar, and repeatedly co-voting on boosted papers).
  6. Profile the seed + expanded accounts (public overview stats, models).

Account creation time is taken from the account's MongoDB ObjectId (first 4 bytes = unix time).
"""

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://huggingface.co/api"
ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"


def get(url, cache_name):
    """Cached GET. With HF_OFFLINE=1, a cache miss returns None instead of fetching."""
    path = RAW / cache_name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if os.environ.get("HF_OFFLINE"):
        print(f"  offline cache miss: {cache_name}")
        return None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hf-upvote-investigate"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                data = None
                break
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            time.sleep(3 * (attempt + 1))
        except urllib.error.URLError:
            time.sleep(3 * (attempt + 1))
    else:
        print(f"  skipped after retries: {url}")
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def created_at(object_id):
    return datetime.fromtimestamp(int(object_id[:8], 16), tz=timezone.utc)


def default_avatar(u):
    return (u.get("avatarUrl") or "").startswith("/avatars/")


def pmap(fn, items, workers=6):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def write_csv(name, rows):
    if not rows:
        return
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="2610.35201")
    ap.add_argument("--end", default="2026-10-29", help="last daily-papers date to scan")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--min-upvotes", type=int, default=10)
    ap.add_argument("--seed-since", default="2026-07-01")
    ap.add_argument("--boosted-min", type=int, default=20, help="seed-ring votes for a paper to count as boosted")
    ap.add_argument("--profile-expanded", action="store_true",
                    help="also fetch overview/models for expanded accounts (2 requests each)")
    args = ap.parse_args()
    seed_since = datetime.fromisoformat(args.seed_since).replace(tzinfo=timezone.utc)

    # 1. daily listings
    end = date.fromisoformat(args.end)
    dates = [(end - timedelta(days=i)).isoformat() for i in range(args.days)]
    listings = pmap(lambda d: (d, get(f"{API}/daily_papers?date={d}&limit=100", f"daily/{d}.json") or []), dates)
    papers = {}
    for d, items in listings:
        for it in items:
            p = it["paper"]
            papers.setdefault(p["id"], {
                "date": d,
                "id": p["id"],
                "title": p.get("title", ""),
                "upvotes": p.get("upvotes", 0),
                "submitted_by": (p.get("submittedOnDailyBy") or {}).get("user", ""),
                "org": (it.get("organization") or p.get("organization") or {}).get("name", ""),
            })
    papers.setdefault(args.target, {"date": "", "id": args.target, "title": "", "upvotes": 0, "submitted_by": "", "org": ""})
    print(f"{len(papers)} papers over {len(dates)} days")

    # 2. upvoters
    todo = [p for p in papers.values() if p["upvotes"] >= args.min_upvotes or p["id"] == args.target]
    voters = dict(pmap(lambda p: (p["id"], get(f"{API}/papers/{p['id']}/upvoters", f"upvoters/{p['id']}.json") or []), todo))
    print(f"fetched upvoters for {len(voters)} papers")

    users = {}
    votes = defaultdict(set)  # user -> paper ids
    for pid, us in voters.items():
        for u in us:
            users[u["user"]] = u
            votes[u["user"]].add(pid)

    # 3. seed ring
    seed = {u["user"] for u in voters[args.target] if created_at(u["_id"]) >= seed_since}
    print(f"seed ring: {len(seed)} of {len(voters[args.target])} upvoters of {args.target}")

    # 4. score papers
    paper_rows = []
    for pid, us in voters.items():
        names = {u["user"] for u in us}
        ring = len(names & seed)
        young = sum(1 for u in us if created_at(u["_id"]) >= seed_since and default_avatar(u))
        p = papers[pid]
        paper_rows.append({**p, "upvoters": len(us), "seed_ring_votes": ring,
                           "seed_ring_frac": round(ring / max(len(us), 1), 3),
                           "young_default_avatar_votes": young})
    paper_rows.sort(key=lambda r: -r["seed_ring_votes"])
    boosted = {r["id"] for r in paper_rows if r["seed_ring_votes"] >= args.boosted_min}

    # 5. expand: non-seed young default-avatar accounts that voted on >= 2 boosted papers
    expanded = set()
    for name, pids in votes.items():
        u = users[name]
        if name in seed or created_at(u["_id"]) < seed_since or not default_avatar(u):
            continue
        if len(pids & boosted) >= 2:
            expanded.add(name)
    print(f"boosted papers: {len(boosted)}; expanded ring candidates: {len(expanded)}")

    # 6. profile accounts
    ring_all = sorted(seed | expanded)

    def profile(name):
        fetch = name in seed or args.profile_expanded
        o = (get(f"{API}/users/{name}/overview", f"users/{name}.json") if fetch else None) or {}
        models = (get(f"{API}/models?author={urllib.parse.quote(name)}&full=true", f"models/{name}.json") if fetch else None) or []
        u = users[name]
        return {
            "user": name,
            "fullname": u.get("fullname", ""),
            "group": "seed" if name in seed else "expanded",
            "created": created_at(u["_id"]).strftime("%Y-%m-%d %H:%M"),
            "default_avatar": default_avatar(u),
            "models": o.get("numModels", 0),
            "datasets": o.get("numDatasets", 0),
            "spaces": o.get("numSpaces", 0),
            "papers": o.get("numPapers", 0),
            "likes": o.get("numLikes", 0),
            "upvotes": o.get("numUpvotes", 0),
            "followers": o.get("numFollowers", 0),
            "following": o.get("numFollowing", 0),
            "orgs": ";".join(x.get("name", "") for x in o.get("orgs", [])),
            "empty_models": sum(1 for m in models if {s["rfilename"] for s in m.get("siblings", [])} <= {".gitattributes"}),
            "model_created": ";".join(sorted({m.get("createdAt", "")[:13] for m in models})),
            "scanned_papers_voted": len(votes[name]),
            "boosted_papers_voted": len(votes[name] & boosted),
        }

    account_rows = pmap(profile, ring_all)

    # target-paper upvoter table (all voters, incl. legitimate ones)
    target_rows = [{"user": u["user"], "fullname": u.get("fullname", ""),
                    "created": created_at(u["_id"]).strftime("%Y-%m-%d %H:%M"),
                    "default_avatar": default_avatar(u), "in_ring": u["user"] in seed}
                   for u in voters[args.target]]

    # per-boosted-paper vote timeline is not public; record co-voting overlap between boosted papers instead
    overlap_rows = []
    bl = sorted(boosted)
    for i, a in enumerate(bl):
        sa = {u["user"] for u in voters[a]} & (seed | expanded)
        for b in bl[i + 1:]:
            sb = {u["user"] for u in voters[b]} & (seed | expanded)
            overlap_rows.append({"paper_a": a, "paper_b": b, "shared_ring_accounts": len(sa & sb)})

    # account creation bursts across seed + expanded
    bursts = Counter(created_at(users[n]["_id"]).strftime("%Y-%m-%d") for n in ring_all)

    OUT.mkdir(exist_ok=True)
    write_csv("papers_scored.csv", paper_rows)
    write_csv("ring_accounts.csv", account_rows)
    write_csv(f"target_upvoters_{args.target}.csv", target_rows)
    write_csv("boosted_overlap.csv", overlap_rows)
    write_csv("ring_creation_days.csv", [{"day": d, "accounts": c} for d, c in sorted(bursts.items())])

    print("\nboosted papers:")
    for r in paper_rows:
        if r["id"] in boosted:
            print(f"  {r['date']} {r['id']} up={r['upvotes']} ring={r['seed_ring_votes']} "
                  f"({r['seed_ring_frac']:.0%}) by={r['submitted_by']} org={r['org']} | {r['title'][:60]}")
    print("\nring creation days:", dict(sorted(bursts.items())))


if __name__ == "__main__":
    main()
