# St John's Chorlton U15 — Season App

A single-file HTML season report for St John's Chorlton JFC U15s, covering two teams:

| Team | Day | League |
|---|---|---|
| **U15 Greens** | Saturday | SDFL Salford & Districts Football League, Division 2 |
| **U15 Whites** | Sunday | Timperley & District JFL |

Live at **[shurdsfield.github.io/stjohns](https://shurdsfield.github.io/stjohns)**

---

## What it does

`generate_season.py` reads three source files per team and produces a single self-contained `index.html`:

- **Results PDF** — FA Full Time fixture/result export (Fixture Group: All)
- **Players PDF** — FA Full Time squad/stats export
- **WhatsApp chat ZIP** — exported group chat (match reports, scorers, MOTM, squad messages)

If no PDFs exist the generator falls back to WhatsApp only. The HTML has a **team switcher** (Greens / Whites) and six tabs: Overview · Results · Players · vs Each Team · Season Story · League Table.

---

## Folder structure

```
teams/
  greens/
    greens.json          ← team config (settings, not data)
    season_config.json   ← generated season data (matches, players, table, story)
    results/             ← drop results PDF here (any filename)
    players/             ← drop players PDF here (any filename)
    chat/                ← stjohns_chat.zip (WhatsApp export)
  whites/
    whites.json
    season_config.json
    results/
    players/
    chat/
generate_season.py       ← generator script
index.html               ← built output (published to GitHub Pages)
```

---

## Requirements

- **Python 3.9+**
- **pdftotext** (from `poppler-utils`) — only needed when parsing PDFs
  ```bash
  brew install poppler          # macOS
  sudo apt install poppler-utils # Linux
  ```

---

## Generating the app

### Combined two-team build (normal usage)

```bash
python3 generate_season.py \
  --team  teams/greens/greens.json \
  --team-2 teams/whites/whites.json
```

The generator skips re-extraction if source files haven't changed, and skips the HTML rebuild if the season data hasn't changed. Both checks are hash-based.

### Force options

```bash
--force-extract   # re-parse source files even if unchanged
--force-build     # rebuild index.html even if season data unchanged
--force           # both of the above
```

### Single-team build

```bash
python3 generate_season.py --team teams/greens/greens.json
python3 generate_season.py --team teams/whites/whites.json
```

### Fetch latest league table from FA Full Time

```bash
python3 generate_season.py --team teams/greens/greens.json --fetch-table
```

Requires `fulltime.league_table_url` to be set in `teams/greens/greens.json`.

---

## Updating source files

### New results / players PDFs

1. Export from FA Full Time (Fixture Group: All)
2. Drop the PDF into `teams/greens/results/` or `teams/greens/players/` (any filename — the generator picks up whatever PDF is in the folder)
3. Re-run the generator

### New WhatsApp chat

1. In WhatsApp: open the group → ⋮ Menu → More → Export chat → Without media
2. Replace `teams/greens/chat/stjohns_chat.zip` (or `teams/whites/chat/stjohns_chat.zip`)
3. Re-run the generator

---

## What the generator extracts from WhatsApp

| Data | Source |
|---|---|
| Match results & scores | Manager result posts |
| Goalscorers | Lines like `Henry 2, Keizo, Noah` posted after result |
| Man of the Match | Lines containing `MOTM` / `MOM` |
| Match summaries | Narrative paragraphs from manager posts |
| Friendly results | Manager result posts (not in FA PDFs) |
| **Player appearances** | Squad messages (e.g. `Squad for Sunday: Conor, Ivan, Harry…`) |

Squad messages are matched to games by opponent name + date. The last squad posted before each game is used (handles "updated squad" messages). Postponed/cancelled games are skipped.

---

## Team config files

`teams/greens/greens.json` and `teams/whites/whites.json` control:

| Key | Purpose |
|---|---|
| `team` | Display name, season, league, division, managers, day |
| `sources` | Folder paths for results, players, chat, output |
| `fulltime.league_table_url` | FA Full Time URL for `--fetch-table` |
| `our_team_name_in_fa` | Team name as it appears on FA Full Time (for table highlighting) |
| `whatsapp.managers` | WhatsApp display names of managers (used to filter result posts) |
| `whatsapp.player_names` | Known player first names (used to identify scorer lines) |
| `whatsapp.season_start_hints` | Date or opponent name to find the start of this season in the chat |
| `whatsapp.name_normalisations` | Spelling fixes applied to scorer lines e.g. `"Conor": "Connor"` |

---

## Season data file

`teams/<team>/season_config.json` is generated automatically but can be hand-edited to fill gaps (unknown scores, missing summaries, etc.). The generator preserves manual edits — it only overwrites fields it can derive from source files.

Key sections:

| Section | Contents |
|---|---|
| `matches` | One entry per game: date, comp, H/A, opponent, score, result, pts, scorers, MOTM, summary |
| `players` | Per-player: lge/cup/fri goals and appearances |
| `league_table` | Final standings (auto-fetched or manually entered) |
| `season_story` | Narrative report: title, sections, highlights, sign-off |
| `stat_cards` | Four headline numbers shown on the Overview tab |
| `highlights` | Season highlight bullets shown on Overview and Season Story tabs |
| `banner_pills` | Info chips shown in the header banner |

---

## Publishing to GitHub Pages

```bash
git add index.html
git commit -m "Describe change"
git push origin main
```

Push to `main` → auto-deploys to `https://shurdsfield.github.io/stjohns` in ~1 minute.

---

## Known notes

- **Two players called Dylan** in Whites: Dylan C = Dylan Chesworth, Dylan H = Dylan Harper. Both normalise to "Dylan" in the Whites app (only one regular Dylan in the Sunday squad).
- **Conor / Connor**: Whites uses Conor Lowe. Greens uses Connor (different player).
- **Elliot / Elliott**: same player (Elliot Swainston) — different spellings appear in source files.
- **Walkovers**: counted as W, 3 pts, score shown as `W/O`.
- **Abandoned games**: excluded from points and played counts (`res: "ABN"`).
- **Postponed games**: shown in Results tab but excluded from standings (`res: "P"`).
- **George Noden**: Sunday GK — different player from Saturday's George.
