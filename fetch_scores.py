#!/usr/bin/env python3
"""
fetch_scores.py  —  Bzzoiro Sports Data fetcher for WC2026 veikkaus
Paginates through ALL results so no matches are missed.
Falls back gracefully if the key is missing or the API is down.
"""

import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

API_KEY  = os.environ.get("BZZOIRO_KEY", "").strip()
BASE_URL = "https://sports.bzzoiro.com/api/v2/events/"
PARAMS   = "date_from=2026-06-11&date_to=2026-06-28&league_id=27&limit=100"

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
        "group":     r.get("group_name"),
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not API_KEY:
        print("❌  BZZOIRO_KEY secret is not set — nothing to fetch.", file=sys.stderr)
        sys.exit(0)   # exit 0 so the workflow doesn't mark the run as failed

    print(f"Fetching WC2026 matches from Bzzoiro…")
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
