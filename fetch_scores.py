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
    """Fetch Polymarket odds, real xG stats, and per-event AI prediction."""
    extras = {"poly": None, "xgHome": None, "xgAway": None, "xgotHome": None, "xgotAway": None, "ai": None}
    if not KEY or not bz_id:
        return extras

    headers = {"Authorization": f"Token {KEY}", "Accept": "application/json"}

    # 1. Polymarket — implied probabilities (0-1), convert to decimal odds
    try:
        req = urllib.request.Request(POLY_URL.format(id=bz_id), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            raw_body = r.read().decode()
        poly = json.loads(raw_body)
        print(f"  poly {bz_id} raw: {raw_body[:200]}")

        def safe_odds(p):
            return round(1 / p, 2) if p and float(p) > 0 else None

        # Schema says prices are implied probabilities — try common key shapes
        if isinstance(poly, list):
            # Array of {outcome, implied_probability, ...} items
            pm = {}
            for item in poly:
                outcome = str(item.get("outcome") or item.get("selection") or "").upper()
                prob = item.get("implied_probability") or item.get("probability") or item.get("price")
                if outcome in ("H", "HOME", "1"):   pm["home"] = prob
                elif outcome in ("D", "DRAW", "X"): pm["draw"] = prob
                elif outcome in ("A", "AWAY", "2"): pm["away"] = prob
            if any(pm.values()):
                extras["poly"] = {k: safe_odds(v) for k, v in pm.items()}
        elif isinstance(poly, dict):
            # Try home/draw/away keys directly
            home_p = poly.get("home") or poly.get("home_prob") or poly.get("home_team_prob")
            draw_p = poly.get("draw") or poly.get("draw_prob")
            away_p = poly.get("away") or poly.get("away_prob") or poly.get("away_team_prob")
            if home_p or draw_p or away_p:
                extras["poly"] = {
                    "home": safe_odds(home_p),
                    "draw": safe_odds(draw_p),
                    "away": safe_odds(away_p),
                }
    except urllib.error.HTTPError as e:
        print(f"  poly {bz_id}: HTTP {e.code} — no Polymarket market for this match")
    except Exception as e:
        print(f"  poly {bz_id}: {e}")

    # 2. Real match xG / xGoT from stats endpoint
    try:
        req = urllib.request.Request(STATS_URL.format(id=bz_id), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            raw_body = r.read().decode()
        stats = json.loads(raw_body)
        print(f"  stats {bz_id} keys: {list(stats.keys()) if isinstance(stats, dict) else type(stats).__name__}")

        def extract_xg(side_data):
            """Try multiple field-name variants for xG."""
            if not isinstance(side_data, dict):
                return None, None
            xg   = side_data.get("xg") or side_data.get("expected_goals") or side_data.get("xG")
            xgot = side_data.get("xgot") or side_data.get("xg_on_target") or side_data.get("xGoT")
            return xg, xgot

        home_side = stats.get("home") or stats.get("home_team") or {}
        away_side = stats.get("away") or stats.get("away_team") or {}

        # Some providers return stats as a flat list: [{team:"home", stat:"xg", value:1.2}, ...]
        if isinstance(stats, list):
            for row in stats:
                team = str(row.get("team") or "").lower()
                stat = str(row.get("stat") or row.get("type") or "").lower()
                val  = row.get("value")
                if team == "home":
                    if stat in ("xg", "expected_goals"): extras["xgHome"] = val
                    if stat in ("xgot", "xg_on_target"): extras["xgotHome"] = val
                elif team == "away":
                    if stat in ("xg", "expected_goals"): extras["xgAway"] = val
                    if stat in ("xgot", "xg_on_target"): extras["xgotAway"] = val
        else:
            xg_h, xgot_h = extract_xg(home_side)
            xg_a, xgot_a = extract_xg(away_side)
            extras["xgHome"]   = xg_h
            extras["xgAway"]   = xg_a
            extras["xgotHome"] = xgot_h
            extras["xgotAway"] = xgot_a

    except urllib.error.HTTPError as e:
        print(f"  stats {bz_id}: HTTP {e.code}")
    except Exception as e:
        print(f"  stats {bz_id}: {e}")

    # 3. Per-event AI prediction
    try:
        req = urllib.request.Request(PRED_DETAIL_URL.format(id=bz_id), headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            pred = json.loads(r.read().decode())
        mr = (pred.get("markets") or {}).get("match_result") or {}
        eg = (pred.get("markets") or {}).get("expected_goals") or {}
        extras["ai"] = {
            "prob1":      mr.get("prob_home"),
            "probX":      mr.get("prob_draw"),
            "prob2":      mr.get("prob_away"),
            "xgHome":     eg.get("home"),
            "xgAway":     eg.get("away"),
            "confidence": (pred.get("model") or {}).get("confidence"),
        }
    except urllib.error.HTTPError as e:
        print(f"  prediction {bz_id}: HTTP {e.code}")
    except Exception as e:
        print(f"  prediction {bz_id}: {e}")

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
