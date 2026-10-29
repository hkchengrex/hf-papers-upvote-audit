"""Fetch (cached) per-account activity feeds and follow lists.

recent-activity returns each upvote/like with a timestamp, which gives vote timing and an account's complete
paper-upvote history (not just the scanned 100+ papers).
"""
import urllib.parse

from investigate import API, get


def activity(user, limit=100):
    """All activity items for a user (upvotes, likes, ...), newest first. None if the fetch failed."""
    q = urllib.parse.urlencode({"limit": limit, "activityType": "all", "feedType": "user", "entity": user})
    data = get(f"{API}/recent-activity?{q}", f"activity/{user}.json")
    return None if data is None else data.get("recentActivity", [])


def paper_upvotes(user):
    """[(paper_id, iso_time)] for every paper the user upvoted, or None if the fetch failed."""
    items = activity(user)
    if items is None:
        return None
    return [(a["target"]["id"], a["time"]) for a in items
            if a.get("type") == "upvote" and a.get("targetType") == "paper" and a.get("target")]


def paper_vote_time(user, paper, max_pages=10):
    """ISO time the user upvoted `paper`, paging back through the upvote-only feed if the default feed (last 100
    items of any type) doesn't reach it. None if not found."""
    for p, t in paper_upvotes(user) or []:
        if p == paper:
            return t
    cursor = None
    for page in range(max_pages):
        params = {"limit": 100, "activityType": "upvote", "feedType": "user", "entity": user}
        if cursor:
            params["cursor"] = cursor
        data = get(f"{API}/recent-activity?{urllib.parse.urlencode(params)}", f"activity_upvotes/{user}_{page}.json")
        if not data:
            return None
        for a in data.get("recentActivity", []):
            if a.get("targetType") == "paper" and (a.get("target") or {}).get("id") == paper:
                return a["time"]
        cursor = data.get("cursor")
        if not cursor or not data.get("recentActivity"):
            return None
    return None


def daily_submit_time(paper, submitter):
    """ISO time `submitter` submitted `paper` to Daily Papers (their 'paper-daily' activity event), or None."""
    for a in activity(submitter) or []:
        if a.get("type") == "paper-daily" and paper in str(a.get("paper") or a.get("target") or a.get("targetUrl") or a):
            return a["time"]
    return None


def followers(user):
    return [u["user"] for u in (get(f"{API}/users/{user}/followers", f"followers/{user}.json") or [])]


def following(user):
    return [u["user"] for u in (get(f"{API}/users/{user}/following", f"following/{user}.json") or [])]
