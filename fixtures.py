import os
import sys
import json
import requests
from datetime import datetime
from collections import defaultdict

# --- Configuration ---
# NEVER hardcode keys/tokens. These are read from environment variables,
# which on GitHub Actions come from repository Secrets (see README.md).
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

TODAY = datetime.now().strftime('%Y-%m-%d')

LIVE_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'SUSP', 'INT'}
FINISHED_STATUSES = {'FT', 'AET', 'PEN'}
HALTED_LABELS = {
    'PST': 'Postponed',
    'CANC': 'Cancelled',
    'ABD': 'Abandoned',
    'AWD': 'Awarded',
    'WO': 'Walkover',
}

NO_MATCHES_TEXT = "No scheduled matches for today in selected leagues."
FB_MESSAGE_LIMIT = 63000  # Facebook's actual cap is 63,206 chars; leave headroom


def get_flag(name, flag_map):
    """Returns the flag from mapping or a default soccer ball."""
    return flag_map.get(name, "⚽")


def build_fixture_string(fixtures, flag_map, leagues_data):
    """Processes fixtures and returns a single formatted string.

    Only fixtures whose league id is in leagues_data are included — this is
    what determines whether there's "a match" worth posting about, not the
    raw global fixture list.
    """
    allowed_ids = {l['id'] for l in leagues_data}
    league_country_map = {l['id']: l.get('country') for l in leagues_data}

    grouped = defaultdict(list)
    for m in fixtures:
        if m['league']['id'] in allowed_ids:
            grouped[m['league']['name']].append(m)

    if not grouped:
        return NO_MATCHES_TEXT

    output = "🚩 Today's matches:\n"
    separator = "-------------------------------------"

    for league_name, matches in grouped.items():
        centered_name = league_name.upper().center(len(separator))
        output += f"\n{separator}\n{centered_name}\n{separator}\n"

        for m in matches:
            home = m['teams']['home']['name']
            away = m['teams']['away']['name']
            country = league_country_map.get(m['league']['id'])

            f_home = get_flag(country or home, flag_map)
            f_away = get_flag(country or away, flag_map)

            status = m['fixture']['status']['short']

            if status == 'NS':
                raw_time = m['fixture']['date']
                # fromisoformat handles whatever UTC offset the API sends,
                # unlike a hardcoded strptime format
                dt = datetime.fromisoformat(raw_time)
                time_str = dt.strftime("%H:%M")
                line = f"{f_home} {home} vs {away} {f_away} ({time_str} UTC)"

            elif status in HALTED_LABELS:
                label = HALTED_LABELS[status]
                line = f"{f_home} {home} - {away} {f_away} ({label})"

            else:
                # Use the live "goals" field, not "score.fulltime" (which is
                # null until the match actually finishes)
                h = m['goals']['home'] if m['goals']['home'] is not None else 0
                a = m['goals']['away'] if m['goals']['away'] is not None else 0
                icon = "🚩" if status in LIVE_STATUSES else "🏁"
                line = f"{f_home} {home} {h} - {a} {f_away} {icon}"

            output += line + "\n"

    return output


def post_to_facebook(message, page_id, access_token):
    """Posts a text message to a Facebook Page's feed via the Graph API."""
    if len(message) > FB_MESSAGE_LIMIT:
        message = message[:FB_MESSAGE_LIMIT] + "\n...(truncated)"

    url = f"https://graph.facebook.com/v21.0/{page_id}/feed"
    resp = requests.post(url, data={"message": message, "access_token": access_token})

    if resp.status_code == 200:
        post_id = resp.json().get("id")
        print(f"Posted to Facebook successfully. Post ID: {post_id}")
    else:
        print(f"Facebook API Error {resp.status_code}: {resp.text}")

    return resp


def main():
    missing = [
        name for name, val in [
            ("FOOTBALL_API_KEY", FOOTBALL_API_KEY),
            ("FB_PAGE_ID", FB_PAGE_ID),
            ("FB_PAGE_ACCESS_TOKEN", FB_PAGE_ACCESS_TOKEN),
        ] if not val
    ]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}. "
              f"Set these as GitHub Actions secrets (see README.md).")
        if "FOOTBALL_API_KEY" in missing:
            sys.exit(1)  # can't fetch fixtures at all without this one

    try:
        with open('flag_mapping.json', 'r', encoding='utf-8') as f:
            flag_map = json.load(f)
        with open('leagues.json', 'r', encoding='utf-8') as f:
            leagues_data = json.load(f)
    except FileNotFoundError as e:
        print(f"Error loading files: {e}")
        sys.exit(1)

    response = requests.get(
        "https://v3.football.api-sports.io/fixtures",
        headers={'x-apisports-key': FOOTBALL_API_KEY},
        params={"date": TODAY}
    )

    if response.status_code != 200:
        print(f"API Error: {response.status_code} - {response.text}")
        sys.exit(1)

    fixtures = response.json().get('response', [])
    match_report = build_fixture_string(fixtures, flag_map, leagues_data)
    print(match_report)

    if match_report == NO_MATCHES_TEXT:
        print("Nothing to post — no matches today in the selected leagues.")
        return

    if FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN:
        post_to_facebook(match_report, FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN)
    else:
        print("FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN not set — skipping Facebook post.")


if __name__ == "__main__":
    main()
