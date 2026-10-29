"""Assign every flagged account a confidence tier. No network requests.

Inputs: cohorts.py (cohorts, members), background.py (concentration and timing vs normal users), and the 801-account
ring from verify.py + extend.py.

Cohort-level tests, each against a background of normal users (not flagged; created before Jul 2026 or custom avatar):
  created together  the cohort itself: >= 20 new default-avatar accounts created within minutes of each other that
                    upvoted largely the same papers
  concentrated      members' median share of their complete paper-upvote history on the cohort's target papers is
                    >= --conc-ratio x the background's (background = normal users who upvoted >= 1 target)
  synchronized      on the cohort's targets, 80% of members' votes arrive within <= --time-ratio x the background's
                    span, and the densest 60-min window holds >= 1.5x the background's share (equal-size samples)

Mass bursts (vote timestamps from activity feeds): >= --burst-votes upvotes on one paper from flagged accounts within
--burst-min minutes. For comparison, the share of sampled normal voters on the same paper whose vote falls in the
burst window is reported.

Tiers
  high            (a) cohort passes all tests AND has >= 3 shared target papers; account upvoted >= 2 targets, or
                  (b) account's vote is part of a mass burst
  coordinated     cohort passes all tests but has only 1-2 target papers; account upvoted a target.
                  These are coordinated, but automated accounts can't be told apart from real people asked to sign up and upvote
  circumstantial  in the ring or in a cohort, but not in a tier above. Evidence: creation within minutes of ring
                  accounts, co-voting with them, and/or an empty random-name model
"""
import argparse, csv, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from activity import paper_upvotes
from investigate import created_at, default_avatar

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--conc-ratio", type=float, default=2.0)
ap.add_argument("--time-ratio", type=float, default=0.25)
ap.add_argument("--burst-votes", type=int, default=50)
ap.add_argument("--burst-min", type=float, default=10)
args = ap.parse_args()


def rows(name):
    return list(csv.DictReader(open(D / name, encoding="utf-8")))


def write_csv(name, rs):
    with open(D / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rs[0].keys())); w.writeheader(); w.writerows(rs)


cohorts = {r["cohort"]: r for r in rows("cohorts.csv")}
papers_up = {r["id"]: r["upvotes"] for r in rows("papers_scored.csv")}
conc = {r["cohort"]: r for r in rows("concentration_full_history.csv")}
timing = {r["cohort"]: r for r in rows("cohort_timing.csv")}
members = defaultdict(list)
for r in rows("cohort_members.csv"):
    members[r["cohort"]].append(r)
ring = {r["user"] for r in rows("verification.csv") if r["confirmed"] == "True"}
ring |= {r["user"] for r in rows("missed_candidates.csv") if r["added"] == "True"}
model_sig = {r["user"] for r in rows("verification.csv") + rows("missed_candidates.csv")
             if r.get("random_name_models") not in (None, "", "0")}

verdicts = []
for k, c in cohorts.items():
    cc, tt = conc.get(k), timing.get(k)
    concentrated = bool(cc) and float(cc["members_median_share"]) >= args.conc_ratio * float(cc["background_median_share"])
    synchronized = bool(tt) and (float(tt["members_hours_for_80pct"]) <= args.time_ratio * float(tt["background_hours_for_80pct"])
                                 and float(tt["members_in_densest_window"]) >= 1.5 * float(tt["background_in_densest_window"]))
    passes = concentrated and synchronized
    verdicts.append({
        "cohort": k, "accounts": c["accounts"], "kind": c["kind"], "target_papers": c["core_papers"],
        "created": f"{c['first_created']} .. {c['last_created']}", "in_ring_pct": c["in_ring_pct"],
        "members_share_on_targets": cc["members_median_share"] if cc else "",
        "background_share_on_targets": cc["background_median_share"] if cc else "",
        "members_hours_80pct": tt["members_hours_for_80pct"] if tt else "",
        "background_hours_80pct": tt["background_hours_for_80pct"] if tt else "",
        "concentrated": concentrated, "synchronized": synchronized,
        "verdict": ("high" if passes and int(c["core_papers"]) >= 3 else "coordinated" if passes else "fails tests"),
    })
write_csv("cohort_verdicts.csv", verdicts)

# ---- mass bursts ----
def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


flagged_users = ring | {m["user"] for ms in members.values() for m in ms}
votes = defaultdict(list)  # paper -> [(time, user)]
for u in flagged_users:
    for p, t in paper_upvotes(u) or []:
        votes[p].append((ts(t), u))
voters, profile = {}, {}
for f in (D / "raw" / "upvoters").glob("*.json"):
    us = json.loads(f.read_text(encoding="utf-8")) or []
    voters[f.stem] = {u["user"] for u in us}
    profile.update((u["user"], u) for u in us)
# normal users = unflagged upvoters with a cached activity feed, created before Jul 2026 or with a custom avatar
# (the same filter as background.py)
since = datetime(2026, 7, 1, tzinfo=timezone.utc)
bg_feeds = {}
for f in (D / "raw" / "activity").glob("*.json"):
    u = f.stem
    if u in profile and u not in flagged_users and (created_at(profile[u]["_id"]) < since or not default_avatar(profile[u])):
        bg_feeds[u] = paper_upvotes(u)

bursts, in_burst = [], set()
w = args.burst_min * 60
for p, vs in votes.items():
    vs.sort()
    i = 0
    while i < len(vs):
        j = i
        while j + 1 < len(vs) and vs[j + 1][0] - vs[i][0] <= w:
            j += 1
        if j - i + 1 >= args.burst_votes:
            # extend to the maximal run of votes <= w apart from the window start, then skip past it
            t0, t1 = vs[i][0], vs[j][0]
            us = [u for _, u in vs[i:j + 1]]
            bg_on_paper = [(u, f) for u, f in bg_feeds.items() if f and any(q == p for q, _ in f)]
            bg_in = sum(1 for u, f in bg_on_paper if any(q == p and t0 <= ts(t) <= t1 for q, t in f))
            bursts.append({"paper": p, "votes": len(us), "start_utc": datetime.utcfromtimestamp(t0).strftime("%Y-%m-%d %H:%M"),
                           "minutes": round((t1 - t0) / 60, 1), "paper_upvotes": papers_up.get(p, ""),
                           "normal_voters_sampled": len(bg_on_paper), "normal_voters_in_window": bg_in})
            in_burst.update((u, p) for u in us)
            i = j + 1
        else:
            i += 1
burst_users = {u for u, _ in in_burst}

tier = {}
for u in burst_users:
    tier[u] = "high"
for v in verdicts:
    for m in members[v["cohort"]]:
        hits = int(m["targets_voted"])
        t = ("high" if v["verdict"] == "high" and hits >= 2 else
             "coordinated" if v["verdict"] in ("high", "coordinated") and hits >= 1 else "")
        rank = {"high": 0, "coordinated": 1, "": 2}
        if t and rank[t] < rank[tier.get(m["user"], "")]:
            tier[m["user"]] = t
for m in (m for ms in members.values() for m in ms):
    tier.setdefault(m["user"], "circumstantial")
for u in ring:
    tier.setdefault(u, "circumstantial")

out = [{"user": u, "tier": t, "in_ring": u in ring, "in_mass_burst": u in burst_users,
        "empty_random_name_model": u in model_sig}
       for u, t in sorted(tier.items(), key=lambda x: (x[1], x[0]))]
write_csv("account_tiers.csv", out)
bursts.sort(key=lambda b: -b["votes"])
write_csv("mass_bursts.csv", bursts)
print(f"mass bursts (>= {args.burst_votes} flagged votes within {args.burst_min:.0f} min): {len(bursts)} on "
      f"{len({b['paper'] for b in bursts})} papers, {len(burst_users)} accounts")
for b in bursts[:15]:
    print(f"  {b['paper']} {b['start_utc']} {b['votes']:>3} votes in {b['minutes']:>4} min (paper has {b['paper_upvotes']}) | "
          f"normal voters in window: {b['normal_voters_in_window']} of {b['normal_voters_sampled']} sampled")
nb = sum(b["normal_voters_sampled"] for b in bursts)
print(f"  all bursts: normal voters in window {sum(b['normal_voters_in_window'] for b in bursts)} of {nb} sampled")

print("cohort verdicts:")
for v in verdicts:
    print(f"  #{v['cohort']:>2} n={v['accounts']:>3} {v['kind']:>12} t={v['target_papers']:>2} ring {v['in_ring_pct']:>3}% | "
          f"conc {v['concentrated']!s:>5} sync {v['synchronized']!s:>5} -> {v['verdict']}")
tc = Counter(tier.values())
print("\naccount tiers:", dict(tc))
for t in ("high", "coordinated", "circumstantial"):
    us = [u for u, x in tier.items() if x == t]
    print(f"  {t}: {len(us)} (ring {sum(1 for u in us if u in ring)}, mass burst {sum(1 for u in us if u in burst_users)}, "
          f"empty random-name model {sum(1 for u in us if u in model_sig)})")

# per-paper votes by tier
papers = {r["id"]: r for r in rows("papers_scored.csv")}
prow = []
for pid, p in papers.items():
    us = [u["user"] for u in json.loads((D / "raw" / "upvoters" / f"{pid}.json").read_text(encoding="utf-8")) or []]
    n = Counter(tier.get(u, "") for u in us)
    if n["high"] + n["coordinated"] + n["circumstantial"]:
        prow.append({k: p[k] for k in ("date", "id", "title", "upvotes", "submitted_by", "org")} |
                    {"high": n["high"], "coordinated": n["coordinated"], "circumstantial": n["circumstantial"],
                     "high_plus_coordinated_pct": round(100 * (n["high"] + n["coordinated"]) / max(len(us), 1), 1),
                     "all_flagged_pct": round(100 * (n["high"] + n["coordinated"] + n["circumstantial"]) / max(len(us), 1), 1)})
prow.sort(key=lambda r: -(r["high"] + r["coordinated"]))
write_csv("papers_by_tier.csv", prow)
print("\ntop papers by high+coordinated votes:")
for r in prow[:20]:
    print(f"  {r['date']} {r['id']} up={r['upvotes']:>4} high={r['high']:>3} coord={r['coordinated']:>3} circ={r['circumstantial']:>3} "
          f"| high+coord {r['high_plus_coordinated_pct']}% all {r['all_flagged_pct']}%")
