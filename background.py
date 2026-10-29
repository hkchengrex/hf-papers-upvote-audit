"""Compare flagged cohorts against a clean background of normal users.

Background: upvoters seen in the scan who were not flagged by any method AND (created before --since OR have a
custom avatar). The ring and cohorts are all new default-avatar accounts, so this excludes undetected members.

1. Concentration (no requests)
   multi-paper cohorts: among accounts that upvoted >= 1 of the cohort's target papers, the mean fraction of the
     targets they upvoted (cohort members vs background)
   all cohorts: the share of an account's scanned upvotes that go to the cohort's targets, among accounts that
     upvoted >= 1 target (cohort members vs background)
2. Vote timing (requests): for target papers, sample up to --per-paper background voters, fetch their activity
   feeds, and compare how tightly votes arrive: the share of a group's votes on a paper that fall inside the
   densest --window-min window for that group, normalized by the group's size on that paper.
"""
import argparse, csv, json, random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from activity import paper_upvotes
from investigate import created_at, default_avatar

ROOT = Path(__file__).resolve().parent
D = ROOT / "data"

ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2026-07-01")
ap.add_argument("--per-paper", type=int, default=15)
ap.add_argument("--ring-papers", type=int, default=15, help="top ring-boosted papers to time")
ap.add_argument("--window-min", type=float, default=60)
args = ap.parse_args()
since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)


def rows(name):
    return list(csv.DictReader(open(D / name, encoding="utf-8")))


def write_csv(name, rs):
    with open(D / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rs[0].keys())); w.writeheader(); w.writerows(rs)


users, voted, voters = {}, defaultdict(set), {}
for f in (D / "raw" / "upvoters").glob("*.json"):
    us = json.loads(f.read_text(encoding="utf-8")) or []
    voters[f.stem] = [u["user"] for u in us]
    for u in us:
        users[u["user"]] = u
        voted[u["user"]].add(f.stem)

# "flagged votes" in the timing test = cohort members + the 801-account ring
ring = {r["user"] for r in rows("verification.csv") if r["confirmed"] == "True"}
ring |= {r["user"] for r in rows("missed_candidates.csv") if r["added"] == "True"}
tiers = ring | {r["user"] for r in rows("cohort_members.csv")}
flagged = set(tiers)
flagged |= {r["user"] for r in rows("missed_candidates.csv")}
flagged |= {r["user"] for r in rows("ring_accounts.csv")}
flagged |= {r["user"] for r in rows("verification.csv") if r["group"] != "control"}
background = {u for u in users if u not in flagged
              and (created_at(users[u]["_id"]) < since or not default_avatar(users[u]))}
print(f"background accounts: {len(background)} of {len(users)} upvoters")

cohorts = rows("cohorts.csv")
members = defaultdict(list)
for r in rows("cohort_members.csv"):
    members[r["cohort"]].append(r["user"])

# ---- 1. concentration ----
conc = []
for c in cohorts:
    tgt = set(c["core_paper_ids"].split(";"))
    mem = members[c["cohort"]]
    bg = [u for u in background if voted[u] & tgt]
    frac_t = lambda us: mean(len(voted[u] & tgt) / len(tgt) for u in us) if us else 0
    share = lambda us: mean(len(voted[u] & tgt) / len(voted[u]) for u in us) if us else 0
    hit = [u for u in mem if voted[u] & tgt]
    conc.append({"cohort": c["cohort"], "kind": c["kind"], "targets": len(tgt), "members": len(mem),
                 "background_voters_of_targets": len(bg),
                 "members_frac_of_targets": round(frac_t(hit), 3), "background_frac_of_targets": round(frac_t(bg), 3),
                 "members_share_on_targets": round(share(hit), 3), "background_share_on_targets": round(share(bg), 3)})
write_csv("concentration_vs_background.csv", conc)
print("\nconcentration (members vs background):")
for r in conc:
    print(f"  #{r['cohort']:>2} {r['kind']:>12} t={r['targets']:>2} | frac of targets {r['members_frac_of_targets']:.0%} vs "
          f"{r['background_frac_of_targets']:.0%} | share of their upvotes on targets {r['members_share_on_targets']:.0%} vs "
          f"{r['background_share_on_targets']:.0%} (bg n={r['background_voters_of_targets']})")

# ---- 2. vote timing ----
ring_papers = [r["id"] for r in rows("papers_ring_final.csv")][: args.ring_papers]
cohort_papers = {p for c in cohorts if c["in_ring_pct"] == "0" for p in c["core_paper_ids"].split(";")}
papers = sorted(set(ring_papers) | cohort_papers)
rng = random.Random(0)
sample = {p: rng.sample(sorted(set(voters.get(p, [])) & background),
                        min(args.per_paper, len(set(voters.get(p, [])) & background))) for p in papers}
to_fetch = sorted({u for us in sample.values() for u in us})
print(f"\ntiming: {len(papers)} papers, fetching {len(to_fetch)} background activity feeds")
with ThreadPoolExecutor(6) as ex:
    feeds = dict(zip(to_fetch, ex.map(paper_upvotes, to_fetch)))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def vote_time(u, p):
    feed = feeds.get(u) if u in feeds else paper_upvotes(u)
    for q, t in feed or []:
        if q == p:
            return ts(t)
    return None


def densest(times, w):
    times = sorted(times)
    best, j = 0, 0
    for i in range(len(times)):
        while times[i] - times[j] > w:
            j += 1
        best = max(best, i - j + 1)
    return best


timing = []
for p in papers:
    flag_t = [t for t in (vote_time(u, p) for u in voters.get(p, []) if u in tiers) if t]
    bg_t = [t for t in (vote_time(u, p) for u in sample[p]) if t]
    if len(flag_t) < 10 or len(bg_t) < 5:
        continue
    # compare equal-size groups: subsample both to the smaller size
    k = min(len(bg_t), len(flag_t))
    sub_f = [rng.sample(flag_t, k) for _ in range(50)]
    sub_b = [rng.sample(bg_t, k) for _ in range(50)]
    w = args.window_min * 60
    span80 = lambda tl: (lambda s, n: min(s[i + n - 1] - s[i] for i in range(len(s) - n + 1)) / 3600)(sorted(tl), max(int(len(tl) * 0.8), 1))
    timing.append({"paper": p, "flagged_votes": len(flag_t), "background_sampled": len(bg_t), "compared_n": k,
                   "flagged_in_densest_window": round(median(densest(s, w) / k for s in sub_f), 2),
                   "background_in_densest_window": round(median(densest(s, w) / k for s in sub_b), 2),
                   "flagged_hours_for_80pct": round(median(span80(s) for s in sub_f), 1),
                   "background_hours_for_80pct": round(median(span80(s) for s in sub_b), 1)})
write_csv("timing_vs_background.csv", timing)
print(f"\ntiming (equal-size groups; share of votes inside the densest {args.window_min:.0f}-min window; hours for 80% of votes):")
for r in timing:
    print(f"  {r['paper']} n={r['flagged_votes']:>3}/{r['background_sampled']:>2} | densest window {r['flagged_in_densest_window']:.0%} vs "
          f"{r['background_in_densest_window']:.0%} | 80% within {r['flagged_hours_for_80pct']}h vs {r['background_hours_for_80pct']}h")
if timing:
    print(f"  median over papers: densest window {median(r['flagged_in_densest_window'] for r in timing):.0%} vs "
          f"{median(r['background_in_densest_window'] for r in timing):.0%}; 80% within "
          f"{median(r['flagged_hours_for_80pct'] for r in timing)}h vs {median(r['background_hours_for_80pct'] for r in timing)}h")

# ---- 2b. vote timing per cohort: the cohort's own members' votes on its targets vs background ----
ctiming = []
for c in cohorts:
    spans_m, spans_b, dens_m, dens_b = [], [], [], []
    for p in c["core_paper_ids"].split(";"):
        if p not in sample:
            continue
        m_t = [t for t in (vote_time(u, p) for u in members[c["cohort"]]) if t]
        b_t = [t for t in (vote_time(u, p) for u in sample[p]) if t]
        k = min(len(m_t), len(b_t))
        if k < 5:
            continue
        w = args.window_min * 60
        span80 = lambda tl: (lambda s, n: min(s[i + n - 1] - s[i] for i in range(len(s) - n + 1)) / 3600)(sorted(tl), max(int(len(tl) * 0.8), 1))
        sm = [rng.sample(m_t, k) for _ in range(30)]
        sb = [rng.sample(b_t, k) for _ in range(30)]
        spans_m.append(median(span80(s) for s in sm)); spans_b.append(median(span80(s) for s in sb))
        dens_m.append(median(densest(s, w) / k for s in sm)); dens_b.append(median(densest(s, w) / k for s in sb))
    if spans_m:
        ctiming.append({"cohort": c["cohort"], "papers_timed": len(spans_m),
                        "members_hours_for_80pct": round(median(spans_m), 2), "background_hours_for_80pct": round(median(spans_b), 2),
                        "members_in_densest_window": round(median(dens_m), 2), "background_in_densest_window": round(median(dens_b), 2)})
if ctiming:
    write_csv("cohort_timing.csv", ctiming)
    print("\nper-cohort timing on own targets (members vs background; hours for 80%, densest-window share):")
    for r in ctiming:
        print(f"  #{r['cohort']:>2} papers={r['papers_timed']} | {r['members_hours_for_80pct']}h vs {r['background_hours_for_80pct']}h | "
              f"{r['members_in_densest_window']:.0%} vs {r['background_in_densest_window']:.0%}")

# ---- 3. concentration on full upvote histories ----
# share of each account's complete paper-upvote history that goes to the cohort's targets, for cohort members vs
# the background users (with fetched feeds) who upvoted >= 1 target
full = []
for c in cohorts:
    tgt = set(c["core_paper_ids"].split(";"))

    def share(u):
        feed = feeds.get(u) if u in feeds else paper_upvotes(u)
        ps = {p for p, _ in feed or []}
        return len(ps & tgt) / len(ps) if ps & tgt else None

    m = [s for s in (share(u) for u in members[c["cohort"]]) if s is not None]
    b = [s for s in (share(u) for u in feeds if feeds[u] and {p for p, _ in feeds[u]} & tgt) if s is not None]
    if m and len(b) >= 5:
        full.append({"cohort": c["cohort"], "kind": c["kind"], "targets": len(tgt),
                     "members_n": len(m), "members_median_share": round(median(m), 3),
                     "background_n": len(b), "background_median_share": round(median(b), 3)})
if full:
    write_csv("concentration_full_history.csv", full)
    print("\nshare of complete paper-upvote history on cohort targets (median, members vs background):")
    for r in full:
        print(f"  #{r['cohort']:>2} {r['kind']:>12} t={r['targets']:>2} | {r['members_median_share']:.0%} (n={r['members_n']}) vs "
              f"{r['background_median_share']:.0%} (n={r['background_n']})")
