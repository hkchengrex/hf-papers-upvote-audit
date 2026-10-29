"""Summarize patterns in the outputs of investigate.py (reads data/*.csv and cached upvoter lists)."""
import csv, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"
BURSTS = {  # account-creation batches seen in ring_creation_days.csv
    "A Jul29-Aug03": ("2026-07-29", "2026-08-03"),
    "B Aug13-17": ("2026-08-13", "2026-08-17"),
    "C Sep04-10": ("2026-09-04", "2026-09-10"),
}


def batch(day):
    for k, (a, b) in BURSTS.items():
        if a <= day <= b:
            return k
    return "other"


def main():
    acc = list(csv.DictReader(open(D / "ring_accounts.csv", encoding="utf-8")))
    grp = {a["user"]: a for a in acc}
    print("accounts by group x batch:")
    print(" ", Counter((a["group"], batch(a["created"][:10])) for a in acc))

    core = {a["user"] for a in acc if batch(a["created"][:10]) != "other"}
    print(f"core ring (seed+expanded created in burst batches): {len(core)}")

    papers = list(csv.DictReader(open(D / "papers_scored.csv", encoding="utf-8")))
    rows = []
    for p in papers:
        us = json.loads((D / "raw" / "upvoters" / f"{p['id']}.json").read_text(encoding="utf-8")) or []
        names = [u["user"] for u in us]
        c = [n for n in names if n in core]
        b = Counter(batch(grp[n]["created"][:10]) for n in c)
        rows.append({**{k: p[k] for k in ("date", "id", "upvotes", "submitted_by", "org")},
                     "core_votes": len(c), "core_frac": round(len(c) / max(len(names), 1), 3),
                     **{k: b.get(k, 0) for k in BURSTS}})
    rows.sort(key=lambda r: -r["core_votes"])
    with open(D / "papers_core_ring.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    hist = Counter(min(r["core_votes"], 20) // 5 * 5 for r in rows)
    print("papers (>=100 upvotes) by core-ring votes, bucketed:", dict(sorted(hist.items())))
    print("\ntop papers by core-ring votes:")
    for r in rows[:16]:
        print(f"  {r['date']} {r['id']} up={r['upvotes']:>4} core={r['core_votes']:>3} ({r['core_frac']:.0%}) "
              f"A/B/C={r['A Jul29-Aug03']}/{r['B Aug13-17']}/{r['C Sep04-10']} by={r['submitted_by']} org={r['org']}")
    first = min((r["date"] for r in rows if r["core_votes"] >= 20), default=None)
    print("\nearliest paper with >=20 core votes:", first)
    print("papers before 2026-08-01 with any core vote:", sum(1 for r in rows if r["date"] < "2026-08-01" and r["core_votes"]))


if __name__ == "__main__":
    main()
