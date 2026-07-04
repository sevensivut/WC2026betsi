#!/usr/bin/env python3
"""
fetch_scores.py — WC2026 Veikkauskilpailu
Fetches group-stage + knockout data from Bzzoiro and writes results.json + knockout.json.
Includes AI predictions, Polymarket odds, and xG/xGoT per match.
"""

import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEY      = os.environ.get("BZZOIRO_KEY", "").strip()
BASE     = "https://sports.bzzoiro.com/api/v2/events/"
PARAMS   = "date_from=2026-06-11&date_to=2026-07-28&league_id=27&limit=100"
KO_PARAMS = "season_id=188&limit=100"
OUT      = "results.json"
KO_FILE  = "knockout.json"

POLY_URL        = "https://sports.bzzoiro.com/api/v2/events/{id}/polymarket/"
STATS_URL       = "https://sports.bzzoiro.com/api/v2/events/{id}/stats/"
PRED_DETAIL_URL = "https://sports.bzzoiro.com/api/v2/events/{id}/prediction/"

# ---------------------------------------------------------------------------
# Team name normalisation (bidirectional + strip)
# ---------------------------------------------------------------------------
ALIASES = {
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Cabo Verde":           "Cape Verde",
    "Cape Verde":           "Cape Verde",
    "Côte d'Ivoire":        "Ivory Coast",
    "Ivory Coast":          "Ivory Coast",
    "DR Congo":             "Congo DR",
    "Congo DR":             "Congo DR",
    "Czechia":              "Czech Republic",
    "Czech Republic":       "Czech Republic",
    "USA":                  "United States",
    "United States":        "United States",
    "Türkiye":              "Turkey",
    "Turkey":               "Turkey",
    "Curaçao":              "Curaçao",
    "South Korea":          "Korea Republic",
    "Korea Republic":       "Korea Republic",
}

STATUS = {
    "ft":          "FT",
    "finished":    "FT",
    "aet":         "FT",
    "penalties":   "FT",
    "inprogress":  "LIVE",
    "live":        "LIVE",
    "1st_half":    "LIVE",
    "2nd_half":    "LIVE",
    "extratime":   "LIVE",
    "extra_time":  "LIVE",
    "halftime":    "HT",
    "ht":          "HT",
    "notstarted":  "NS",
    "postponed":   "PST",
    "cancelled":   "CANC",
}

FINISHED_STATUSES = {"ft", "finished", "aet", "penalties"}
LIVE_STATUSES     = {"inprogress", "live", "1st_half", "2nd_half", "extratime", "extra_time"}
HT_STATUSES       = {"ht", "halftime"}

ROUND_MAP = {
    "round of 32": "R32",
    "round of 16": "R16",
    "quarter-final": "QF", "quarter-finals": "QF", "quarterfinal": "QF",
    "semi-final": "SF", "semi-finals": "SF", "semifinal": "SF",
    "final": "F", "3rd place": "3RD", "third place": "3RD",
}

def norm(name):
    name = name.strip() if name else ""
    return ALIASES.get(name, name) if name else ""

def rarity_bonus(n):
    if n <= 0: return 0
    if n == 1: return 4
    if n == 2: return 3
    if n <= 5: return 1
    return 0

# ---------------------------------------------------------------------------
# Paginated fetch helper
# ---------------------------------------------------------------------------
def get_all(url):
    results, page = [], 1
    while url:
        print(f"  page {page}: {url[:80]}")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {KEY}",
            "Accept":        "application/json",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        batch = data.get("results", [])
        results.extend(batch)
        print(f"    +{len(batch)} (total {len(results)}/{data.get('count','?')})")
        url = data.get("next")
        page += 1
        if url:
            time.sleep(0.3)
    return results

# ---------------------------------------------------------------------------
# Build a normalised game record from one Bzzoiro event
# ---------------------------------------------------------------------------
def make_game(r):
    hs  = r.get("home_score")
    as_ = r.get("away_score")
    raw_status = (r.get("status") or "").strip().lower()
    status = STATUS.get(raw_status, raw_status.upper() if raw_status else "NS")

    # Safety net: if scores exist but status says NS, force FT
    if hs is not None and as_ is not None and status == "NS":
        status = "FT"

    weather = r.get("weather") or {}

    return {
        "home":      norm(r.get("home_team", "")),
        "away":      norm(r.get("away_team", "")),
        "homeScore": int(hs)  if hs  is not None else None,
        "awayScore": int(as_) if as_ is not None else None,
        "htHome":    r.get("home_score_ht"),
        "htAway":    r.get("away_score_ht"),
        "status":    status,
        "date":      (r.get("event_date") or "")[:10],
        "group":     r.get("group_name"),
        "minute":    r.get("current_minute"),
        "period":    r.get("period", ""),
        "weather": {
            "temp": weather.get("temperature_c"),
            "wind": weather.get("wind_speed"),
            "desc": weather.get("description", ""),
        } if weather else None,
        "attendance": r.get("attendance"),
        "homeId":    r.get("home_team_id"),
        "awayId":    r.get("away_team_id"),
        "bzId":      r.get("id"),
    }

# ---------------------------------------------------------------------------
# Fetch extras per match (Polymarket, xG, AI prediction)
# ---------------------------------------------------------------------------
def fetch_match_extras(bz_id):
    """Fetch Polymarket odds + xG/xGoT + AI prediction for a single match."""
    extras = {"poly": None, "xgHome": None, "xgAway": None,
              "xgotHome": None, "xgotAway": None, "ai": None}
    if not KEY or not bz_id:
        return extras

    headers = {"Authorization": f"Token {KEY}", "Accept": "application/json"}

    # 1. Polymarket Odds
    try:
        req = urllib.request.Request(POLY_URL.format(id=bz_id), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            poly = json.loads(r.read().decode())
        if poly.get("home"):
            extras["poly"] = {
                "home": round(1 / poly["home"], 2) if poly["home"] else None,
                "draw": round(1 / poly["draw"], 2) if poly.get("draw") else None,
                "away": round(1 / poly["away"], 2) if poly["away"] else None,
            }
    except Exception:
        pass

    # 2. xG / xGoT Stats
    try:
        req = urllib.request.Request(STATS_URL.format(id=bz_id), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            stats = json.loads(r.read().decode())
        home_stats = stats.get("home", {})
        away_stats = stats.get("away", {})
        extras["xgHome"] = home_stats.get("xg") or home_stats.get("expected_goals")
        extras["xgAway"] = away_stats.get("xg") or away_stats.get("expected_goals")
        extras["xgotHome"] = home_stats.get("xgot") or home_stats.get("xg_on_target")
        extras["xgotAway"] = away_stats.get("xgot") or away_stats.get("xg_on_target")
    except Exception:
        pass

    # 3. Per-event AI Prediction (v2)
    try:
        req = urllib.request.Request(PRED_DETAIL_URL.format(id=bz_id), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            pred = json.loads(r.read().decode())
        mr = pred.get("markets", {}).get("match_result", {})
        eg = pred.get("markets", {}).get("expected_goals", {})
        extras["ai"] = {
            "prob1": mr.get("prob_home"),
            "probX": mr.get("prob_draw"),
            "prob2": mr.get("prob_away"),
            "xgHome": eg.get("home"),
            "xgAway": eg.get("away"),
            "confidence": pred.get("model", {}).get("confidence"),
        }
    except Exception:
        pass

    return extras

# ---------------------------------------------------------------------------
# Knockout stage sync
# ---------------------------------------------------------------------------
def update_knockout(raw_events=None):
    """Sync knockout.json with Bzzoiro data for R32 → Final.

    raw_events: the already-fetched list of raw API event dicts from the
    group-stage fetch (which includes knockout matches because league_id=27
    covers the full tournament). Passing these in avoids a second API call
    and ensures round_name is available (make_game() strips it).
    Falls back to a fresh season_id fetch if not provided.
    """
    if not os.path.exists(KO_FILE):
        print(f"⚠️  {KO_FILE} not found — skipping knockout sync", file=sys.stderr)
        return

    with open(KO_FILE, encoding="utf-8") as f:
        ko = json.load(f)

    if raw_events is not None:
        print(f"🏆 Syncing knockout from {len(raw_events)} already-fetched events…")
        events = raw_events
    else:
        # Fallback: fetch independently
        if not KEY:
            print("⚠️  BZZOIRO_KEY not set — skipping knockout sync", file=sys.stderr)
            return
        print("🏆 Fetching knockout events (separate call)…")
        events = get_all(f"{BASE}?{KO_PARAMS}")

    # Group Bzzoiro events by normalised round name
    by_round = {}
    for e in events:
        rname = (e.get("round_name") or "").strip().lower()
        rid = ROUND_MAP.get(rname)
        if not rid:
            continue
        by_round.setdefault(rid, []).append(e)

    print(f"  Rounds found in API data: {list(by_round.keys())}") 

    updated_count = 0

    for round_obj in ko.get("rounds", []):
        rid = round_obj.get("id")
        bz_events = by_round.get(rid, [])
        if not bz_events:
            continue

        # Build lookup by normalised team names
        bz_by_teams = {}
        for e in bz_events:
            h = norm(e.get("home_team", ""))
            a = norm(e.get("away_team", ""))
            if h and a:
                bz_by_teams[f"{h}|{a}"] = e
                bz_by_teams[f"{a}|{h}"] = e

        for m in round_obj.get("matches", []):
            e = None
            if m.get("home") and m.get("away"):
                key = f"{norm(m['home'])}|{norm(m['away'])}"
                e = bz_by_teams.get(key)

            if not e:
                continue  # Can't resolve yet (placeholder slot)

            # Always refresh team names and scores/status
            m["home"] = norm(e.get("home_team")) or m["home"]
            m["away"] = norm(e.get("away_team")) or m["away"]

            hs = e.get("home_score")
            as_ = e.get("away_score")
            raw_status = (e.get("status") or "").strip().lower()

            if hs is not None and as_ is not None and raw_status in FINISHED_STATUSES:
                m["played"]  = True
                m["actualA"] = int(hs)
                m["actualB"] = int(as_)
                # Determine winner — handle AET draws resolved by penalty shootout
                if int(hs) > int(as_):
                    m["winner"] = m["home"]
                elif int(as_) > int(hs):
                    m["winner"] = m["away"]
                else:
                    # Scores level — check penalty_shootout field
                    pen = e.get("penalty_shootout") or {}
                    pen_home = pen.get("home_score") if isinstance(pen, dict) else None
                    pen_away = pen.get("away_score") if isinstance(pen, dict) else None
                    if pen_home is not None and pen_away is not None:
                        m["winner"] = m["home"] if pen_home > pen_away else m["away"]
                        m["penHome"] = int(pen_home)
                        m["penAway"] = int(pen_away)
                    else:
                        # Can't determine winner yet (data pending)
                        m["winner"] = None

                # Recalculate every player's points
                n_correct = 0
                for p, pr in m.get("preds", {}).items():
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

            elif raw_status in LIVE_STATUSES or raw_status in HT_STATUSES:
                m["played"]  = True
                m["actualA"] = int(hs)
                m["actualB"] = int(as_)
                m["status"]  = "LIVE" if raw_status in LIVE_STATUSES else "HT"
                m["minute"]  = e.get("current_minute")
                # Don't set winner or recalculate points until FT

    ko["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(KO_FILE, "w", encoding="utf-8") as f:
        json.dump(ko, f, ensure_ascii=False, separators=(",", ":"))

    print(f"✅  {KO_FILE} synced — {updated_count} matches updated")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not KEY:
        print("❌  BZZOIRO_KEY not set — nothing to fetch", file=sys.stderr)
        sys.exit(0)

    # ── Group Stage ──────────────────────────────────────────────
    print("🌍 Fetching WC2026 group-stage events…")
    raw = get_all(f"{BASE}?{PARAMS}")

    # Skip placeholder knockout slots (team names like "2A", "2B")
    games = [make_game(r) for r in raw
             if len(r.get("home_team", "")) > 2 and len(r.get("away_team", "")) > 2]

    # Fetch odds, xG, AI for active/upcoming matches only
    print("🔍 Fetching odds & xG for active/upcoming matches...")
    enriched = 0
    for g in games:
        if g["status"] in ("NS", "LIVE", "HT"):
            extras = fetch_match_extras(g["bzId"])
            g.update(extras)
            enriched += 1
            time.sleep(0.25)  # Rate limit protection
    print(f"  ✅ Enriched {enriched} matches")

    finished = [g for g in games if g["homeScore"] is not None]
    live     = [g for g in games if g["status"] == "LIVE"]
    ht_games = [g for g in games if g["status"] == "HT"]

    print(f"\n📊 {len(finished)} finished | {len(live)} live | {len(ht_games)} HT | {len(games)} total")

    out = {
        "updated":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":        "Bzzoiro Sports Data",
        "liveCount":     len(live) + len(ht_games),
        "finishedCount": len(finished),
        "games":         games,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print(f"✅  {OUT} written")

    # ── Knockout Stage ───────────────────────────────────────────
    # Pass the already-fetched raw events so update_knockout() doesn't
    # need to make a separate API call. The league_id=27 fetch with
    # date_to=2026-07-28 already includes all knockout stage matches,
    # and crucially the raw events still have round_name intact.
    update_knockout(raw)


if __name__ == "__main__":
    main()
