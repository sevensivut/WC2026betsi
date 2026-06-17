#!/usr/bin/env python3
"""
fetch_scores.py  —  Bzzoiro Sports Data fetcher for WC2026 veikkaus
Paginates through ALL results so no matches are missed.
Falls back gracefully if the key is missing or the API is down.
Includes "Smart Fetch" logic to minimize API calls.
"""

import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

API_KEY  = os.environ.get("BZZOIRO_KEY", "").strip()
BASE_URL = "https://sports.bzzoiro.com/api/v2/events/"
# FIX: Use season_id=188 to bypass the API's 7-day date window limit!
PARAMS   = "season_id=188&limit=200" 

# ---------------------------------------------------------------------------
# Team-name normalisation  →  match the names used in the predictions CSV
# ---------------------------------------------------------------------------
ALIASES = {
    # Bzzoiro-specific
    "south korea":          "Korea Republic",
    "usa":                  "United States",
    "côte d'ivoire":        "Ivory Coast",
    "cote d'ivoire":        "Ivory Coast",
    "cabo verde":           "Cape Verde",
    "dr congo":             "Congo DR",
    "bosnia & herzegovina": "Bosnia and Herzegovina",
    # Other API variants
    "turkey":               "Türkiye",
    "republic of korea":    "Korea Republic",
    "united states of america": "United States",
    "democratic republic of the congo": "Congo DR",
    "ir iran":              "Iran",
    "czech republic":       "Czechia",
    "curacao":              "Curaçao",
    "bosnia-herzegovina":   "Bosnia and Herzegovina",
}

def norm(name: str) -> str:
    if not name:
        return ""
    return ALIASES.get(name.strip().lower(), name.strip())

# ---------------------------------------------------------------------------
# Smart Fetch Logic
# ---------------------------------------------------------------------------
def is_fetch_needed(file_path="results.json"):
    if not os.path.exists(file_path):
        return True

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return True

    games = data.get("games", [])
    if not games:
        return True

    now = datetime.now(timezone.utc)
    
    for g in games:
        event_date_str = g.get("event_date")
        if not event_date_str:
            continue
            
        try:
            # Parse ISO 8601 date
            match_time = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
            
        diff_minutes = (match_time - now).total_seconds() / 60
        
        # 1. Match starts in the next 60 minutes
        if 0 < diff_minutes <= 60:
            return True
            
        # 2. Match started recently (within last 4 hours to cover ET/Penalties and final API updates)
        hours_since_start = -diff_minutes / 60
        if 0 <= hours_since_start <= 4.0:
            return True
            
        # 3. Match is currently LIVE
        if g.get("status") == "LIVE":
            return True
            
    return False

# ---------------------------------------------------------------------------
# Paginated fetch
# ---------------------------------------------------------------------------
def fetch_all_matches():
    url = f"{BASE_URL}?{PARAMS}"
    all_results = []
    page = 1

    while url:
        print(f"  Fetching page {page}: {url}")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Token {API_KEY}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            raise RuntimeError(f"HTTP {e.code}: {body}")

        results = data.get("results", [])
        all_results.extend(results)
        print(f"    → {len(results)} matches (total so far: {len(all_results)} / {data.get('count','?')})")

        url = data.get("next")  # None when we've reached the last page
        page += 1
        if url:
            time.sleep(0.3)   # be polite

    return all_results

# ---------------------------------------------------------------------------
# Convert to the shape the HTML expects
# ---------------------------------------------------------------------------
STATUS_MAP = {
    "ft":          "FT",
    "finished":    "FT",
    "inprogress":  "LIVE",
    "live":        "LIVE",
    "1h":          "LIVE",
    "ht":          "LIVE",
    "2h":          "LIVE",
    "et":          "LIVE",
    "notstarted":  "NS",
    "postponed":   "PST",
    "cancelled":   "CANC",
}

def normalise_match(r):
    status_raw = (r.get("status") or "").lower()
    status     = STATUS_MAP.get(status_raw, status_raw.upper())
    home_score = r.get("home_score")
    away_score = r.get("away_score")
    return {
        "home":      norm(r.get("home_team", "")),
        "away":      norm(r.get("away_team", "")),
        "homeScore": int(home_score) if home_score is not None else None,
        "awayScore": int(away_score) if away_score is not None else None,
        "status":    status,
        "date":      (r.get("event_date") or "")[:10],
        "event_date": r.get("event_date", ""),  # Added full timestamp for Smart Fetch logic
        "group":     r.get("group_name"),
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not API_KEY:
        print("❌  BZZOIRO_KEY secret is not set — nothing to fetch.", file=sys.stderr)
        sys.exit(0)   # exit 0 so the workflow doesn't mark the run as failed

    # --- SMART FETCH CHECK ---
    if not is_fetch_needed():
        print("💤 No active or upcoming matches in the immediate window. Skipping API call to be courteous.")
        sys.exit(0)
        
    print(f"⚽ Match window active. Fetching WC2026 matches from Bzzoiro…")
    
    try:
        raw = fetch_all_matches()
    except Exception as e:
        print(f"❌  API fetch failed: {e}", file=sys.stderr)
        sys.exit(0)

    games = [normalise_match(r) for r in raw]

    # Skip placeholder knockout-stage slots ("2A", "2B", etc.)
    games = [g for g in games if not (len(g["home"]) <= 2 or len(g["away"]) <= 2)]

    finished = [g for g in games if g["homeScore"] is not None]
    live     = [g for g in games if g["status"] == "LIVE"]

    output = {
        "updated":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source":        "Bzzoiro Sports Data",
        "finishedCount": len(finished),
        "liveCount":     len(live),
        "games":         games,
    }

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(f"✅  results.json written — {len(finished)} finished, {len(live)} live, {len(games)} total")

if __name__ == "__main__":
    main()
