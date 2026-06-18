#!/usr/bin/env python3
"""
fetch_scores.py — WC2026 Veikkauskilpailu
Fetches all WC2026 group-stage data from Bzzoiro (paginated) and writes results.json.
Also tries to fetch AI predictions for upcoming matches.
"""

import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

KEY      = os.environ.get("BZZOIRO_KEY", "").strip()
BASE     = "https://sports.bzzoiro.com/api/v2/events/"
PARAMS   = "date_from=2026-06-11&date_to=2026-06-28&league_id=27&limit=100"
PRED_URL = "https://sports.bzzoiro.com/api/predictions/?upcoming=true&league_id=27&limit=100"
OUT      = "results.json"

# ---------------------------------------------------------------------------
# Team name normalisation  (Bzzoiro → names used in data.json)
# ---------------------------------------------------------------------------
ALIASES = {
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Cabo Verde":           "Cape Verde",
    "Côte d'Ivoire":        "Ivory Coast",
    "DR Congo":             "Congo DR",
    "Czechia":              "Czech Republic",
    "USA":                  "United States",
    "Türkiye":              "Turkey",      # data.json uses "Turkey" from original CSV mapping
    "Curaçao":              "Curaçao",     # keep accented form — matches data.json
    "South Korea":          "Korea Republic",
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

def norm(name):
    return ALIASES.get(name, name) if name else ""

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
    raw_status = (r.get("status") or "").strip().lower()  # ← .strip() added!
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
# Fetch AI predictions
# ---------------------------------------------------------------------------
def fetch_predictions():
    if not KEY:
        return []
    try:
        print("  Fetching predictions…")
        req = urllib.request.Request(PRED_URL, headers={
            "Authorization": f"Token {KEY}",
            "Accept":        "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        raw = data if isinstance(data, list) else data.get("results", [])
        preds = []
        for p in raw:
            # Bzzoiro predictions shape — adapt field names as needed
            home = norm(p.get("home_team") or p.get("home") or "")
            away = norm(p.get("away_team") or p.get("away") or "")
            if not home or not away:
                continue
            preds.append({
                "home":  home,
                "away":  away,
                "prob1": p.get("home_win_prob") or p.get("prob_1") or p.get("win_prob"),
                "probX": p.get("draw_prob")     or p.get("prob_x"),
                "prob2": p.get("away_win_prob") or p.get("prob_2") or p.get("lose_prob"),
                "xgHome": p.get("expected_goals_home") or p.get("xg_home"),
                "xgAway": p.get("expected_goals_away") or p.get("xg_away"),
            })
        print(f"  Got {len(preds)} predictions")
        return preds
    except Exception as e:
        print(f"  Predictions fetch failed (non-fatal): {e}", file=sys.stderr)
        return []

POLY_URL        = "https://sports.bzzoiro.com/api/v2/events/{id}/polymarket/"
STATS_URL       = "https://sports.bzzoiro.com/api/v2/events/{id}/stats/"
PRED_DETAIL_URL = "https://sports.bzzoiro.com/api/v2/events/{id}/prediction/"

def fetch_match_extras(bz_id):
    """Fetch Polymarket odds + xG/xGoT for a single match."""
    extras = {"poly": None, "xgHome": None, "xgAway": None, "xgotHome": None, "xgotAway": None}
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
# Main
# ---------------------------------------------------------------------------
def main():
    if not KEY:
        print("❌  BZZOIRO_KEY not set — nothing to fetch", file=sys.stderr)
        sys.exit(0)

    print(f"🌍 Fetching WC2026 events…")
    raw = get_all(f"{BASE}?{PARAMS}")

    # Skip placeholder knockout slots (team names like "2A", "2B")
    games = [make_game(r) for r in raw
             if len(r.get("home_team","")) > 2 and len(r.get("away_team","")) > 2]

    # Fetch odds & xG for active/upcoming matches only
    print("🔍 Fetching odds & xG for active/upcoming matches...")
    for g in games:
        if g["status"] in ("NS", "LIVE", "HT"):
            extras = fetch_match_extras(g["bzId"])
            g.update(extras)
            time.sleep(0.2)  # Rate limit protection

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

if __name__ == "__main__":
    main()
