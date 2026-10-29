"""Scripted filler repositories: the account-management fingerprint behind the ring.

Many flagged accounts own an empty model and/or dataset with a generated name (adjective-noun-6hex, e.g.
`formal-village-2567b1`). This script finds every such repo in the cached activity feeds ("publish" events) and
model listings, groups their creation times into runs, and compares with normal users.

Inputs (no new requests except --dataset-sample dataset lookups): data/raw/activity, data/raw/models,
data/raw/upvoters, account_tiers.csv and the other flag lists.

Outputs
  fingerprint_repos.csv   every generated-name repo: owner, owner's tier, type, created, owner's account age
  fingerprint_runs.csv    creation runs (>= --run-min repos, consecutive creations <= --run-gap seconds apart)
  fingerprint_likes.csv   repos liked by the most flagged accounts, vs normal accounts with fetched feeds
"""
import argparse, csv, json, random, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from investigate import API, get

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"
RAW = D / "raw"
NAME = re.compile(r"^[a-z]+-[a-z]+-[0-9a-f]{6}$")

ap = argparse.ArgumentParser()
ap.add_argument("--run-gap", type=float, default=60, help="max seconds between consecutive creations in a run")
ap.add_argument("--run-min", type=int, default=10)
ap.add_argument("--dataset-sample", type=int, default=30, help="generated-name datasets to check for emptiness")
args = ap.parse_args()


def rows(name):
    return list(csv.DictReader(open(D / name, encoding="utf-8")))


def write_csv(name, rs):
    with open(D / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rs[0].keys())); w.writeheader(); w.writerows(rs)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")


tier = {r["user"]: r["tier"] for r in rows("account_tiers.csv")}
flagged = set(tier) | {r["user"] for r in rows("cohort_members.csv")} | {r["user"] for r in rows("missed_candidates.csv")}
flagged |= {r["user"] for r in rows("verification.csv") if r["group"] != "control"}

created = {}
for f in (RAW / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        created[u["user"]] = datetime.fromtimestamp(int(u["_id"][:8], 16), timezone.utc)

feeds = {}
for f in (RAW / "activity").glob("*.json"):
    d = json.loads(f.read_text(encoding="utf-8"))
    if d:
        feeds[f.stem] = d.get("recentActivity", [])


def group(u):
    return tier.get(u) or ("other flagged" if u in flagged else "unflagged")


# ---- generated-name repos: from feeds (models + datasets) and model listings (models, with file lists) ----
repos = {}  # repo id -> row
for u, acts in feeds.items():
    for a in acts:
        rid = a.get("repoId") or (a.get("repoData") or {}).get("id") or ""
        if a.get("type") == "publish" and a.get("repoType") in ("model", "dataset") and NAME.match(rid.split("/")[-1]):
            repos[rid] = {"repo": rid, "type": a["repoType"], "owner": u, "created": ts(a["time"]), "files": ""}
for f in (RAW / "models").glob("*.json"):
    for m in json.loads(f.read_text(encoding="utf-8")) or []:
        if NAME.match(m["id"].split("/")[-1]):
            r = repos.setdefault(m["id"], {"repo": m["id"], "type": "model", "owner": f.stem,
                                           "created": ts(m["createdAt"]), "files": ""})
            r["files"] = ";".join(sorted(s["rfilename"] for s in m.get("siblings", [])))
            r["downloads"], r["likes"] = m.get("downloads", 0), m.get("likes", 0)
            r["modified_after_creation"] = m.get("lastModified") != m.get("createdAt")

# emptiness check for a sample of datasets
ds = sorted(r for r, v in repos.items() if v["type"] == "dataset" and v["owner"] in flagged)
for rid in random.Random(0).sample(ds, min(args.dataset_sample, len(ds))):
    info = get(f"{API}/datasets/{rid}", f"datasets/{rid.replace('/', '__')}.json")
    if info:
        repos[rid]["files"] = ";".join(sorted(s["rfilename"] for s in info.get("siblings", [])))
        repos[rid]["downloads"], repos[rid]["likes"] = info.get("downloads", 0), info.get("likes", 0)

out = []
for r in sorted(repos.values(), key=lambda r: r["created"]):
    c = created.get(r["owner"])
    out.append({"repo": r["repo"], "type": r["type"], "owner": r["owner"], "owner_tier": group(r["owner"]),
                "created_utc": fmt(r["created"]), "owner_created_utc": fmt(c) if c else "",
                "owner_age_days": round((r["created"] - c).total_seconds() / 86400, 2) if c else "",
                "files": r["files"], "downloads": r.get("downloads", ""), "likes": r.get("likes", ""),
                "modified_after_creation": r.get("modified_after_creation", "")})
write_csv("fingerprint_repos.csv", out)

# ---- creation runs ----
runs = []
for typ in ("dataset", "model"):
    rs = sorted((r for r in repos.values() if r["type"] == typ), key=lambda r: r["created"])
    cur = rs[:1]
    for r in rs[1:] + [None]:
        if r and (r["created"] - cur[-1]["created"]).total_seconds() <= args.run_gap:
            cur.append(r)
            continue
        if len(cur) >= args.run_min:
            gaps = [(b["created"] - a["created"]).total_seconds() for a, b in zip(cur, cur[1:])]
            owners = [x["owner"] for x in cur]
            ages = [(x["created"] - created[x["owner"]]).total_seconds() / 86400 for x in cur if x["owner"] in created]
            runs.append({"type": typ, "start_utc": fmt(cur[0]["created"]), "end_utc": fmt(cur[-1]["created"]),
                         "minutes": round((cur[-1]["created"] - cur[0]["created"]).total_seconds() / 60, 1),
                         "repos": len(cur), "accounts": len(set(owners)),
                         "median_gap_s": median(gaps), "max_gap_s": max(gaps),
                         "owners_high": sum(1 for o in set(owners) if tier.get(o) == "high"),
                         "owners_unflagged": sum(1 for o in set(owners) if o not in flagged),
                         "owner_created_from": fmt(min(created[o] for o in owners if o in created)),
                         "owner_created_to": fmt(max(created[o] for o in owners if o in created)),
                         "owner_age_days_median": round(median(ages), 1) if ages else ""})
        cur = [r] if r else []
write_csv("fingerprint_runs.csv", runs)

# ---- filler likes ----
likers = defaultdict(set)
for u, acts in feeds.items():
    for a in acts:
        if a.get("type") == "like":
            likers[a.get("repoId") or (a.get("repoData") or {}).get("id")].add(u)
n_flag = sum(1 for u in feeds if u in flagged)
n_norm = len(feeds) - n_flag
top = sorted(likers, key=lambda r: -sum(1 for u in likers[r] if u in flagged))[:15]
write_csv("fingerprint_likes.csv", [{"repo": r, "flagged_likers": sum(1 for u in likers[r] if u in flagged),
                                     "flagged_feeds": n_flag,
                                     "unflagged_likers": sum(1 for u in likers[r] if u not in flagged),
                                     "unflagged_feeds": n_norm} for r in top])

# ---- summary ----
mine = [r for r in out if r["owner"] in flagged]
print(f"generated-name repos: {len(out)} ({Counter(r['type'] for r in out)}); owned by flagged accounts: {len(mine)} "
      f"on {len({r['owner'] for r in mine})} accounts")
print("  owner groups:", dict(Counter(r["owner_tier"] for r in out)))
mods = [r for r in mine if r["type"] == "model" and r["files"]]
dss = [r for r in mine if r["type"] == "dataset" and r["files"]]
print(f"  models with file list: {len(mods)}, empty: {sum(1 for r in mods if r['files'] == '.gitattributes')}, "
      f"downloads>0: {sum(1 for r in mods if r['downloads'])}, likes>0: {sum(1 for r in mods if r['likes'])}, "
      f"modified after creation: {sum(1 for r in mods if r['modified_after_creation'] is True)}")
print(f"  sampled datasets: {len(dss)}, empty: {sum(1 for r in dss if r['files'] in ('.gitattributes', ''))}, "
      f"file sets: {dict(Counter(r['files'] for r in dss))}")
unflag_pub = [(u, a) for u, acts in feeds.items() if u not in flagged for a in acts
              if a.get("type") == "publish" and a.get("repoType") in ("model", "dataset")]
print(f"  unflagged accounts with feeds: {n_norm}; their model/dataset publishes: {len(unflag_pub)}; generated-name: "
      f"{sum(1 for r in out if r['owner'] not in flagged)} {[(r['owner'], r['created_utc'], r['owner_created_utc']) for r in out if r['owner'] not in flagged]}")
print("\nruns:")
for r in runs:
    print(f"  {r['type']:>7} {r['start_utc']} -> {r['end_utc'][11:]} ({r['minutes']} min): {r['repos']} repos on {r['accounts']} accounts, "
          f"gap median {r['median_gap_s']:.0f}s max {r['max_gap_s']:.0f}s | high {r['owners_high']}, unflagged {r['owners_unflagged']} | "
          f"owners created {r['owner_created_from'][:10]}..{r['owner_created_to'][:10]}, median age {r['owner_age_days_median']} d")
both = {r["owner"] for r in out if r["type"] == "model"} & {r["owner"] for r in out if r["type"] == "dataset"}
print(f"accounts with both a generated-name model and dataset: {len(both)}; any: {len({r['owner'] for r in out})}")
print("\nmost-liked by flagged accounts (flagged of", n_flag, "feeds vs unflagged of", n_norm, "):")
for r in top[:5]:
    print(f"  {r}: {sum(1 for u in likers[r] if u in flagged)} vs {sum(1 for u in likers[r] if u not in flagged)}")
