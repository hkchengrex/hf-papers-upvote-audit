"""Seed-free detection of coordinated account cohorts among new upvoters. No network requests.

Accounts: every upvoter seen in the scan created on/after --since that still has the default avatar.
Two accounts are linked if they were created within --window minutes of each other AND their sets of upvoted
(scanned) papers have Jaccard similarity >= --jaccard. Connected components with >= --min-size accounts are cohorts.

Null models (each repeated --null-runs times; votes are kept, creation times are shuffled, cohorts re-detected):
  day   shuffle creation times among accounts created on the same calendar day (tests minute-level timing only)
  week  shuffle creation times among accounts created in the same 7-day block (tests "created together" at all)

Concentration: for each cohort, the share of its members' upvotes that land on the cohort's target papers (papers
>= 50% of members upvoted), compared with the same share for ordinary new accounts created within +-7 days
(not in any cohort).
"""
import argparse, csv, json, random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from investigate import created_at, default_avatar

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2026-07-01")
ap.add_argument("--window", type=float, default=10)
ap.add_argument("--jaccard", type=float, default=0.5)
ap.add_argument("--min-size", type=int, default=20)
ap.add_argument("--null-runs", type=int, default=20)
args = ap.parse_args()
since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

users, voted = {}, defaultdict(set)
for f in (D / "raw" / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        users[u["user"]] = u
        voted[u["user"]].add(f.stem)
papers = {r["id"]: r for r in csv.DictReader(open(D / "papers_scored.csv", encoding="utf-8"))}

ring = {r["user"] for r in csv.DictReader(open(D / "verification.csv", encoding="utf-8")) if r["confirmed"] == "True"}
ring |= {r["user"] for r in csv.DictReader(open(D / "missed_candidates.csv", encoding="utf-8")) if r["added"] == "True"}

acc = [n for n, u in users.items() if created_at(u["_id"]) >= since and default_avatar(u)]
times = {n: created_at(users[n]["_id"]).timestamp() for n in acc}


def jac(a, b):
    return len(a & b) / len(a | b)


def find_cohorts(times):
    order = sorted(acc, key=lambda n: times[n])
    parent = {n: n for n in acc}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    w = args.window * 60
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if times[b] - times[a] > w:
                break
            if jac(voted[a], voted[b]) >= args.jaccard:
                parent[root(a)] = root(b)
    comps = defaultdict(list)
    for n in acc:
        comps[root(n)].append(n)
    return [c for c in comps.values() if len(c) >= args.min_size]


def describe(c, times):
    ts = sorted(times[n] for n in c)
    k80 = max(int(len(ts) * 0.8), 1)
    span80 = min(ts[i + k80 - 1] - ts[i] for i in range(len(ts) - k80 + 1)) / 3600
    pc = Counter(p for n in c for p in voted[n])
    core = sorted((p for p, v in pc.items() if v >= 0.5 * len(c)), key=lambda p: papers[p]["date"])
    kind = "multi-paper" if len(core) >= 3 else "single-paper" if len(core) == 1 else "few-paper"
    return ts, span80, pc, core, kind


cohorts = find_cohorts(times)

def run_null(block_of):
    """Shuffle creation times within blocks, re-detect; return per-run (cohorts, accounts, kind counter)."""
    blocks = defaultdict(list)
    for n in acc:
        blocks[block_of(times[n])].append(n)
    out = []
    for run in range(args.null_runs):
        rng = random.Random(run)
        shuffled = {}
        for ns in blocks.values():
            ts = [times[n] for n in ns]
            rng.shuffle(ts)
            shuffled.update(zip(ns, ts))
        nc = find_cohorts(shuffled)
        out.append((len(nc), sum(len(c) for c in nc), Counter(describe(c, shuffled)[4] for c in nc),
                    sum(len(c) for c in nc if describe(c, shuffled)[4] == "multi-paper")))
    return out


nulls = {"day": run_null(lambda t: int(t // 86400)), "week": run_null(lambda t: int(t // (7 * 86400)))}

# peers for the concentration comparison: new accounts not flagged by any method
flagged = {n for c in cohorts for n in c} | ring
flagged |= {r["user"] for r in csv.DictReader(open(D / "missed_candidates.csv", encoding="utf-8"))}
flagged |= {r["user"] for r in csv.DictReader(open(D / "verification.csv", encoding="utf-8")) if r["group"] != "control"}
ordinary = [n for n in acc if n not in flagged]

rows = []
for k, c in enumerate(sorted(cohorts, key=lambda c: min(times[n] for n in c))):
    ts, span80, pc, core, kind = describe(c, times)
    rows.append({
        "cohort": k + 1,
        "accounts": len(c),
        "first_created": datetime.fromtimestamp(ts[0], timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "last_created": datetime.fromtimestamp(ts[-1], timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "hours_for_80pct": round(span80, 1),
        "median_gap_min": round(median(b - a for a, b in zip(ts, ts[1:])) / 60, 1),
        "in_ring_pct": round(100 * sum(1 for n in c if n in ring) / len(c)),
        "core_papers": len(core),
        "core_paper_ids": ";".join(core),
        "core_paper_dates": ";".join(papers[p]["date"] for p in core),
        "votes_on_core": ";".join(str(pc[p]) for p in core),
        "share_of_core_upvotes": ";".join(f"{pc[p] / int(papers[p]['upvotes']):.0%}" for p in core),
        "kind": kind,
    })
    # concentration vs ordinary new accounts from the same +-7 days
    tgt = {p for p, v in pc.items() if v >= 0.5 * len(c)}
    share = lambda ns: (sum(len(voted[n] & tgt) for n in ns) / max(sum(len(voted[n]) for n in ns), 1))
    peers = [n for n in ordinary if abs(times[n] - ts[0]) <= 7 * 86400]
    rows[-1]["vote_share_on_targets"] = round(share(c), 3)
    rows[-1]["ordinary_peers"] = len(peers)
    rows[-1]["ordinary_share_on_targets"] = round(share(peers), 4)
    rows[-1]["peers_voting_2plus_targets_pct"] = round(100 * sum(1 for n in peers if len(voted[n] & tgt) >= 2) / max(len(peers), 1), 2)
with open(D / "cohorts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open(D / "cohort_members.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["user", "cohort", "created", "in_ring", "targets_voted", "upvotes_scanned"])
    for r, c in zip(rows, sorted(cohorts, key=lambda c: min(times[n] for n in c))):
        tgt = set(r["core_paper_ids"].split(";"))
        for n in sorted(c, key=lambda n: times[n]):
            w.writerow([n, r["cohort"], created_at(users[n]["_id"]).strftime("%Y-%m-%d %H:%M"), n in ring,
                        len(voted[n] & tgt), len(voted[n])])

print(f"{len(acc)} new default-avatar accounts; {len(cohorts)} cohorts (>= {args.min_size}) covering "
      f"{sum(len(c) for c in cohorts)} accounts")
real_kinds = Counter(r["kind"] for r in rows)
real_multi_acc = sum(r["accounts"] for r in rows if r["kind"] == "multi-paper")
print("real kinds:", dict(real_kinds), f"| multi-paper accounts {real_multi_acc}")
for name, res in nulls.items():
    kinds = Counter()
    for _, _, k, _ in res:
        kinds.update(k)
    print(f"null[{name}] ({args.null_runs} runs): cohorts median {median(r[0] for r in res)} max {max(r[0] for r in res)}; "
          f"accounts median {median(r[1] for r in res)}; kinds/run { {k: v / args.null_runs for k, v in kinds.items()} }; "
          f"multi-paper accounts max {max(r[3] for r in res)}")
for r in rows:
    print(f"  #{r['cohort']:>2} n={r['accounts']:>3} {r['first_created']}..{r['last_created'][5:]} ring {r['in_ring_pct']:>3}% | "
          f"{r['kind']:>12} {r['core_papers']:>2} | on-targets {r['vote_share_on_targets']:.0%} vs peers "
          f"{r['ordinary_share_on_targets']:.1%} | peers voting 2+ targets {r['peers_voting_2plus_targets_pct']}%")
