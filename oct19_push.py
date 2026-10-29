"""The Oct 19 push: one synchronized round of upvotes on old papers. No network requests (cached feeds).

Takes every flagged account's paper upvotes inside --start .. --end (UTC), takes the --top papers that drew the most
of them (the push targets), and lists each account that voted in the window: creation time, avatar, how many
targets it hit, and how long its votes took. Writes data/oct19_push.csv and prints the summary used in the README.
"""
import argparse, csv, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from activity import paper_upvotes
from investigate import created_at, default_avatar

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="2026-10-19T07:00:00")
ap.add_argument("--end", default="2026-10-19T07:15:00")
ap.add_argument("--top", type=int, default=11)
args = ap.parse_args()
t0, t1 = (datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() for s in (args.start, args.end))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fmt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


tier = {r["user"]: r["tier"] for r in csv.DictReader(open(D / "account_tiers.csv", encoding="utf-8"))}
users = {}
for f in (D / "raw" / "upvoters").glob("*.json"):
    for u in json.loads(f.read_text(encoding="utf-8")) or []:
        users[u["user"]] = u
generated_repo = {r["owner"] for r in csv.DictReader(open(D / "fingerprint_repos.csv", encoding="utf-8"))}

votes = defaultdict(list)  # user -> [(time, paper)] inside the window
for u in tier:
    for p, t in paper_upvotes(u) or []:
        if t0 <= ts(t) < t1:
            votes[u].append((ts(t), p))
per_paper = Counter(p for vs in votes.values() for _, p in vs)
targets = sorted(p for p, _ in per_paper.most_common(args.top))

out = []
for u, vs in votes.items():
    vs.sort()
    out.append({"user": u, "tier": tier[u], "created_utc": fmt(created_at(users[u]["_id"]).timestamp()),
                "default_avatar": default_avatar(users[u]), "generated_name_repo": u in generated_repo,
                "targets_voted": len({p for _, p in vs if p in targets}), "votes_in_window": len(vs),
                "first_vote_utc": fmt(vs[0][0]), "last_vote_utc": fmt(vs[-1][0]),
                "seconds_first_to_last": round(vs[-1][0] - vs[0][0], 1)})
out.sort(key=lambda r: (-r["targets_voted"], r["created_utc"]))
with open(D / "oct19_push.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)

full = [r for r in out if r["targets_voted"] == len(targets)]
created = sorted(created_at(users[r["user"]]["_id"]).timestamp() for r in full)
gaps = [b - a for a, b in zip(created, created[1:])]
secs = [r["seconds_first_to_last"] for r in full]
per_vote = [r["seconds_first_to_last"] / (r["votes_in_window"] - 1) for r in full]
print(f"window {args.start} .. {args.end} UTC: {len(out)} flagged accounts voted; votes per paper: "
      f"{', '.join(f'{p} {n}' for p, n in per_paper.most_common(args.top + 3))}")
print(f"votes per account: {dict(Counter(r['votes_in_window'] for r in out).most_common())}")
print(f"{len(full)} accounts voted on all {len(targets)}: tiers {dict(Counter(r['tier'] for r in full))}, "
      f"default avatar {sum(r['default_avatar'] for r in full)}, generated-name repo {sum(r['generated_name_repo'] for r in full)}")
print(f"  created {fmt(created[0])} .. {fmt(created[-1])}, median gap {median(gaps) / 60:.1f} min")
print(f"  votes per account in the window: {dict(Counter(r['votes_in_window'] for r in full).most_common())}; median "
      f"{median(secs):.0f} s from first to last vote, {median(per_vote):.1f} s between consecutive votes")
