#!/usr/bin/env python3
"""
Interactive scaffold for a new team season.

Run with:  python3 add_team.py

Prompts for team details and available data sources (FA results PDF,
FA players PDF, WhatsApp chat, FA Full Time table URL), then:

  • creates  teams/<team_id>/{results,players,chat}/  directories
  • writes   teams/<team_id>/<team_id>.json  team config
  • prints   the file-drop instructions and the build command

If no PDFs are available, the team will be built from the WhatsApp
chat alone — generate_season.py handles missing-PDF fallbacks.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def ask(prompt, default=None, required=True):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        ans = input(f"{prompt}{suffix}: ").strip()
        if not ans and default is not None:
            return default
        if ans:
            return ans
        if not required:
            return ""
        print("  (required)")


def ask_yes_no(prompt, default=True):
    d = "Y/n" if default else "y/N"
    while True:
        ans = input(f"{prompt} [{d}]: ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def ask_list(prompt, example):
    print(f"{prompt}")
    print(f"  (comma-separated, e.g. {example})")
    raw = input("  > ").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def slugify(s):
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def main():
    print("=" * 60)
    print("  Add a new team season")
    print("=" * 60)
    print()

    full_name   = ask("Team full name (e.g. 'St John's Chorlton U16 Greens')")
    short_name  = ask("Short name (e.g. 'Greens')")
    season      = ask("Season (e.g. '2026–27')")
    league      = ask("League name", default="")
    division    = ask("Division", default="TBC")
    managers    = ask("Manager(s)", default="")
    day         = ask("Match day (Saturday/Sunday)", default="Saturday")

    suggested = slugify(short_name) or slugify(full_name)
    team_id   = ask("Team folder id", default=suggested)

    team_dir = os.path.join("teams", team_id)
    if os.path.exists(team_dir):
        if not ask_yes_no(f"\n{team_dir} already exists — overwrite config?", default=False):
            print("Aborted.")
            return 1

    print()
    print("─" * 60)
    print(" Data sources")
    print("─" * 60)
    print(" Tell me what raw data you have for this team. You can")
    print(" drop in files now or later — folders will be created.")
    print()

    has_results = ask_yes_no("FA Full Time results PDF available?", default=False)
    has_players = ask_yes_no("FA Full Time players-stats PDF available?", default=False)
    has_chat    = ask_yes_no("WhatsApp chat export (.zip) available?", default=True)

    if not has_chat and not (has_results or has_players):
        print("\nERROR: at least one source needed (chat zip, or one of the PDFs).")
        return 1

    if not has_results and not has_players:
        print("\nNote: with no PDFs, results and player stats will come from")
        print("      the WhatsApp chat alone. Score capping by FA won't apply.")

    table_url = ""
    if ask_yes_no("\nDo you have a FA Full Time URL for the league table?", default=False):
        table_url = ask("  League table URL", required=True)

    fa_team_name = ""
    if has_results or has_players or table_url:
        fa_team_name = ask(
            "Exact team name as it appears in FA Full Time",
            default=full_name,
        )

    print()
    print("─" * 60)
    print(" WhatsApp parsing")
    print("─" * 60)
    if has_chat:
        wa_managers = ask_list(
            "WhatsApp display names of the managers (used to filter result posts)",
            "SJFC Lucas Nick, steve",
        )
        wa_players = ask_list(
            "Player first-names / short names as they appear in scorer lines",
            "Petr, Dylan H, Dylan C, Max, Micah",
        )
        start_hint = ask(
            "First-match date hint (M/D/YY) or opponent name (to skip prior season)",
            default="",
            required=False,
        )
        season_hints = [h for h in [start_hint] if h]
    else:
        wa_managers, wa_players, season_hints = [], [], []

    # ── create folders ────────────────────────────────────────────
    for sub in ("results", "players", "chat"):
        os.makedirs(os.path.join(team_dir, sub), exist_ok=True)

    # ── build config ──────────────────────────────────────────────
    sources = {
        "results_pdf": f"teams/{team_id}/results/",
        "players_pdf": f"teams/{team_id}/players/",
        "chat_zip":    f"teams/{team_id}/chat/{team_id}_chat.zip",
        "season_config": f"teams/{team_id}/season_config.json",
        "output_html": "index.html",
    }

    cfg = {
        "_comment": f"{day} team config — {full_name}. Generated by add_team.py.",
        "team_id": team_id,
        "team": {
            "name": full_name,
            "short_name": short_name,
            "season": season,
            "league": league,
            "division": division,
            "managers": managers,
            "day": day,
        },
        "sources": sources,
        "fulltime": {
            "league_table_url": table_url or None,
            "notes": [
                "Add or update league_table_url to enable --fetch-table.",
            ] if not table_url else [],
        },
        "our_team_name_in_fa": fa_team_name or full_name,
    }
    if has_chat:
        cfg["whatsapp"] = {
            "managers": wa_managers,
            "player_names": wa_players,
            "season_start_hints": season_hints,
            "name_normalisations": {},
        }

    config_path = os.path.join(team_dir, f"{team_id}.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    # ── next steps ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(" ✓ Created team scaffold")
    print("=" * 60)
    print(f"  config:   {config_path}")
    print(f"  results:  {team_dir}/results/   (drop FA results PDF here)")
    print(f"  players:  {team_dir}/players/   (drop FA players PDF here)")
    print(f"  chat:     {team_dir}/chat/{team_id}_chat.zip   (WhatsApp export)")
    print()
    print("Drop your data files into the folders above, then build with:")
    print()
    print(f"  python3 generate_season.py --team {config_path} \\")
    print(f"    --team-2 teams/greens/greens.json \\")
    print(f"    --force-extract" + (" --fetch-table" if table_url else ""))
    print()
    print("(Use --team alone for a single-team build.)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(130)
