#!/usr/bin/env python3
"""
fetch_scores_knockout_patch.py
Drop-in addition for fetch_scores.py — syncs knockout.json (R32 → Final)
from Bzzoiro using the round_name field, paginated.

Run this AFTER your existing group-stage fetch, or merge update_knockout()
directly into your main fetch_scores.py and call it from main().
"""

import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEY    = os.environ.get("BZZOIRO_KEY", "").strip()
BASE   = "https://sports.bzzoiro.com/api/v2/events/"
PARAMS = "season_id=188&limit=100"   # season_id covers the whole tournament incl. knockouts
KO_FILE = "knockout.json"

ALIASES = {
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Cabo Verde":           "Cape Verde",
    "Côte d'Ivoire":        "Ivory Coast",
    "DR Congo":             "Congo DR",
    "Czechia":              "Czech Republic",
    "USA":                  "United States",
    "Türkiye":              "Turkey",
    "South Korea":          "Korea Republic",
}
def norm(t): return ALIASES.get(t, t) if t else t

ROUND_MAP = {
    "round of 32": "R32",
    "round of 16": "R16",
    "quarter-final": "QF", "quarter-finals": "QF", "quarterfinal": "QF",
    "semi-final": "SF", "semi-finals": "SF", "semifinal": "SF",
    "final": "F",
}

def rarity_bonus(n):
    if n <= 0: return 0
    if n == 1: return 4
    if n == 2: return 3
    if n <= 5: return 1
    return 0

def get_all(url):
    results, page = [], 1
    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {KEY}", "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        results.extend(data.get("results", []))
        url = data.get("next")
        page += 1
        if url: time.sleep(0.3)
    return results

def update_knockout():
    if not KEY:
        print("⚠️  BZZOIRO_KEY not set — skipping knockout sync", file=sys.stderr)
        return

    if not os.path.exists(KO_FILE):
        print(f"⚠️  {KO_FILE} not found — nothing to update", file=sys.stderr)
        return

    with open(KO_FILE, encoding="utf-8") as f:
        ko = json.load(f)

    events = get_all(f"{BASE}?{PARAMS}")

    # Group Bzzoiro events by normalised round name
    by_round = {}
    for e in events:
        rname = (e.get("round_name") or "").strip().lower()
        rid = ROUND_MAP.get(rname)
        if not rid:
            continue
        by_round.setdefault(rid, []).append(e)

    updated_count = 0

    for round_obj in ko["rounds"]:
        rid = round_obj["id"]
        bz_events = by_round.get(rid, [])
        if not bz_events:
            continue

        # Build lookup by Bzzoiro internal event id position (sorted) to
        # align with our match ids — fall back to team-name matching when
        # both home/away are already known (non-placeholder).
        bz_by_teams = {}
        for e in bz_events:
            h, a = norm(e.get("home_team","")), norm(e.get("away_team",""))
            if h and a:
                bz_by_teams[f"{h}|{a}"] = e
                bz_by_teams[f"{a}|{h}"] = e

        for m in round_obj["matches"]:
            e = None
            if m.get("home") and m.get("away"):
                key = f"{m['home']}|{m['away']}"
                e = bz_by_teams.get(key)

            if not e:
                continue  # can't resolve yet (e.g. R16 slot still "3A/B/C")

            # Always refresh team names (placeholders resolve as earlier
            # rounds complete) and scores/status.
            m["home"] = norm(e.get("home_team")) or m["home"]
            m["away"] = norm(e.get("away_team")) or m["away"]

            hs, as_ = e.get("home_score"), e.get("away_score")
            status  = (e.get("status") or "").lower()

            if hs is not None and as_ is not None and status in ("ft", "finished", "aet", "pen"):
                m["played"]  = True
                m["actualA"] = int(hs)
                m["actualB"] = int(as_)
                m["winner"]  = m["home"] if hs > as_ else m["away"]

                # Recalculate every player's points
                n_correct = 0
                for p, pr in m["preds"].items():
                    exact = pr.get("a") == m["actualA"] and pr.get("b") == m["actualB"]
                    correct_adv = pr.get("winner") == m["winner"]
                    pr["pts"]   = 4 if exact else (2 if correct_adv else 0)
                    pr["exact"] = exact
                    if pr["pts"] > 0:
                        n_correct += 1
                bonus = rarity_bonus(n_correct)
                m["oikein"] = n_correct
                m["rarity"] = bonus
                if bonus:
                    for pr in m["preds"].values():
                        if pr["pts"] > 0:
                            pr["pts"] += bonus

                updated_count += 1

    ko["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(KO_FILE, "w", encoding="utf-8") as f:
        json.dump(ko, f, ensure_ascii=False, separators=(",", ":"))

    print(f"✅  knockout.json synced — {updated_count} matches updated from Bzzoiro")


if __name__ == "__main__":
    update_knockout()
