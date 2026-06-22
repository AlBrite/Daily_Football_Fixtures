import os
import sys
import json
import requests
from datetime import datetime

# ---------------------------------------------------------------------------
# Environment / secrets
# ---------------------------------------------------------------------------
FOOTBALL_API_KEY    = os.environ.get("FOOTBALL_API_KEY")
FB_PAGE_ID          = os.environ.get("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

# Injected by the workflow via `workflow_dispatch` inputs
HOME_TEAM = os.environ.get("HOME_TEAM", "").strip()
AWAY_TEAM = os.environ.get("AWAY_TEAM", "").strip()

REGISTRY_PATH = "registry.json"
FLAG_MAP_PATH  = "flag_mapping.json"

FB_MESSAGE_LIMIT = 63000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_flag(name, flag_map):
    return flag_map.get(name, "🏳️")


def find_registry_entry(registry, home, away):
    """Return (index, entry) for the first record matching home & away (case-insensitive)."""
    h, a = home.lower(), away.lower()
    for i, record in enumerate(registry):
        if record.get("home", "").lower() == h and record.get("away", "").lower() == a:
            return i, record
    return None, None


def fetch_fixture(fixture_id):
    url = "https://v3.football.api-sports.io/fixtures"
    resp = requests.get(
        url,
        headers={"x-apisports-key": FOOTBALL_API_KEY},
        params={"id": fixture_id},
    )
    resp.raise_for_status()
    data = resp.json().get("response", [])
    if not data:
        raise ValueError(f"No fixture data returned for id={fixture_id}")
    return data[0]


def format_event_line(event, flag_map):
    """
    Format a single match event into one line, e.g.:
        15' ⚽ Surman   🅰️ Tim Payne
    Supported types: Goal, Card.
    """
    minute  = event.get("time", {}).get("elapsed", "?")
    extra   = event.get("time", {}).get("extra")
    min_str = f"{minute}+{extra}'" if extra else f"{minute}'"

    etype   = event.get("type", "")
    detail  = event.get("detail", "")
    player  = event.get("player", {}).get("name", "")
    assist  = event.get("assist", {}).get("name") or ""

    if etype == "Goal":
        if detail == "Own Goal":
            icon = "⚽ (OG)"
        elif detail == "Penalty":
            icon = "⚽ (P)"
        else:
            icon = "⚽"
        assist_part = f"   🅰️ {assist}" if assist else ""
        return f"{min_str} {icon} {player}{assist_part}"

    if etype == "Card":
        icon = "🟨" if "Yellow" in detail else "🟥"
        return f"{min_str} {icon} {player}"

    # Substitutions and other event types are intentionally omitted from the
    # post to keep it clean; return None to skip.
    return None


def build_match_post(fixture, flag_map):
    """
    Build the full Facebook post string in the requested format:

    🔔 — Full Time

    New Zealand 🇳🇿 1-3 Egypt 🇪🇬

    15' ⚽ Surman   🅰️ Tim Payne
    ...

    ⛳️ 4:3 - 🟨 2:1

    #FIFAWorldCup2026
    """
    teams   = fixture["teams"]
    goals   = fixture["goals"]
    stats   = fixture.get("statistics", [])
    events  = fixture.get("events", [])
    league  = fixture["league"]

    home_name = teams["home"]["name"]
    away_name = teams["away"]["name"]
    home_goals = goals["home"] if goals["home"] is not None else 0
    away_goals = goals["away"] if goals["away"] is not None else 0

    home_flag = get_flag(home_name, flag_map)
    away_flag = get_flag(away_name, flag_map)

    # --- Shots on target & cards from statistics block ---
    def stat_val(team_stats, label):
        for item in team_stats:
            if item.get("type") == label:
                v = item.get("value")
                return int(v) if v is not None else 0
        return 0

    home_stats_raw = next((s["statistics"] for s in stats if s["team"]["name"] == home_name), [])
    away_stats_raw = next((s["statistics"] for s in stats if s["team"]["name"] == away_name), [])

    home_shots = stat_val(home_stats_raw, "Shots on Goal")
    away_shots = stat_val(away_stats_raw, "Shots on Goal")

    # Count yellow cards from events (more reliable than stats block)
    home_yellows = sum(
        1 for e in events
        if e.get("type") == "Card"
        and "Yellow" in e.get("detail", "")
        and e.get("team", {}).get("name") == home_name
    )
    away_yellows = sum(
        1 for e in events
        if e.get("type") == "Card"
        and "Yellow" in e.get("detail", "")
        and e.get("team", {}).get("name") == away_name
    )

    # --- League hashtag ---
    league_name = league.get("name", "Football")
    # Strip spaces & special chars to make a clean hashtag
    hashtag = "#" + "".join(w.capitalize() for w in league_name.replace("-", " ").split())

    # --- Event lines (goals & cards only) ---
    event_lines = []
    for e in events:
        line = format_event_line(e, flag_map)
        if line:
            event_lines.append(line)

    events_block = "\n".join(event_lines) if event_lines else ""

    # --- Assemble ---
    post = (
        f"🔔 — Full Time \n\n"
        f"{home_name} {home_flag} {home_goals}-{away_goals} {away_name} {away_flag} \n"
    )
    if events_block:
        post += f"\n{events_block}\n"

    post += (
        f"\n⛳️ {home_shots}:{away_shots} - 🟨 {home_yellows}:{away_yellows}\n\n"
        f"{hashtag}"
    )

    return post


def post_to_facebook(message):
    if len(message) > FB_MESSAGE_LIMIT:
        message = message[:FB_MESSAGE_LIMIT] + "\n...(truncated)"

    url  = f"https://graph.facebook.com/v21.0/{FB_PAGE_ID}/feed"
    resp = requests.post(url, data={"message": message, "access_token": FB_PAGE_ACCESS_TOKEN})

    if resp.status_code == 200:
        print(f"✅ Posted to Facebook. Post ID: {resp.json().get('id')}")
    else:
        print(f"❌ Facebook API Error {resp.status_code}: {resp.text}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Validate required secrets
    missing = [
        name for name, val in [
            ("FOOTBALL_API_KEY",    FOOTBALL_API_KEY),
            ("FB_PAGE_ID",          FB_PAGE_ID),
            ("FB_PAGE_ACCESS_TOKEN", FB_PAGE_ACCESS_TOKEN),
        ] if not val
    ]
    if missing:
        print(f"Missing secrets: {', '.join(missing)}")
        sys.exit(1)

    if not HOME_TEAM or not AWAY_TEAM:
        print("HOME_TEAM and AWAY_TEAM must be provided via workflow inputs.")
        sys.exit(1)

    # Load registry
    try:
        registry = load_json(REGISTRY_PATH)
    except FileNotFoundError:
        print(f"Registry file not found at '{REGISTRY_PATH}'.")
        sys.exit(1)

    # Find matching record
    idx, record = find_registry_entry(registry, HOME_TEAM, AWAY_TEAM)
    if record is None:
        print(f"No registry entry found for '{HOME_TEAM}' vs '{AWAY_TEAM}'.")
        sys.exit(1)

    fixture_id = record["id"]
    print(f"Found registry entry — fixture ID: {fixture_id}")

    # Load flag map
    try:
        flag_map = load_json(FLAG_MAP_PATH)
    except FileNotFoundError:
        print(f"Flag mapping file not found at '{FLAG_MAP_PATH}'.")
        flag_map = {}

    # Fetch fixture details from API
    print(f"Fetching fixture {fixture_id} from API…")
    fixture = fetch_fixture(fixture_id)

    # Build the post
    post = build_match_post(fixture, flag_map)
    print("\n--- Post preview ---")
    print(post)
    print("--------------------\n")

    # Post to Facebook
    post_to_facebook(post)

    # Remove the entry from the registry and save
    registry.pop(idx)
    save_json(REGISTRY_PATH, registry)
    print(f"✅ Registry entry for '{HOME_TEAM}' vs '{AWAY_TEAM}' removed. "
          f"{len(registry)} record(s) remaining.")


if __name__ == "__main__":
    main()
  
