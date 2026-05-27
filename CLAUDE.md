# St John's Chorlton U15 — Season App · Claude Code Instructions

## Project Overview

A single-file HTML season report app for St John's Chorlton JFC U15s, covering two teams:
- **Saturday team**: U15 Greens — SDFL Salford & Districts Football League, Division 2
- **Sunday team**: U15 Whites — Timperley & District JFL

The app is a self-contained `index.html` file (no external dependencies) published on GitHub Pages at `https://shurdsfield.github.io/stjohns`.

---

## Managers

| Team | Managers |
|---|---|
| Saturday (Greens) | Nick Turner (SJFC Lucas Nick) and Steve Hurdsfield |
| Sunday (Whites) | Mike Lowe (Mike Lowe Football St Johns) and Paul Murray (SJFC Ivan Paul) |

---

## App Structure

The app has a **team switcher toggle** at the top (Greens / Whites) and **6 nav tabs**:

1. **Overview** — stat cards, top scorers table, MOTM table, season highlights
2. **Results** — filterable match table with expandable match reports (tap to expand)
3. **Players** — appearances & goals table + player cards
4. **vs Each Team** — head-to-head record against every opponent
5. **Season Story** — narrative report
6. **League Table** — final standings

---
## required file inputs

there are 3 folders where the raw data comes from. the data should be extracted into a structured format for the app
if there is no data the user should be prompted when generating the app. if the raw data changes the structured data should be regenerated

- chat: the whatsapp chat group where we get the goalscorers and match reports from and friendly results extract
- players: a list of each player and their appearances. both 'started' and 'Bench unused' count as an appearance
- results: a list of all league and cup results but usually doesnt include friendlies
---

## Technical Constraints

- **Single self-contained HTML file** — no external scripts, fonts, CDN links
- **No localStorage or sessionStorage** — all state in JS variables
- **No overflow:auto or overflow:scroll on any ancestor of sticky elements** — sticky headers don't work inside scroll containers
- **No duplicate `const` declarations** in the same script scope — causes fatal parse error
- **Always test with Node.js** before shipping: extract the `<script>` block and run with `new Function('document', script)(mockDocument)`
- ** there needs to be a way of rebuilding the app from the data

---




### Goal source of truth
- if **no pdfs** exist default to just whatsapp chat
- **League and cup goals**: FA Full Time official PDF (Fixture Group: All) from the players pdf
- **Friendly goals**: WhatsApp match reports in the chat folder

### results source of truth
- **League and cup**: FA Full Time official PDF (Fixture Group: All) from the results pdf
- **Friendly**: WhatsApp match reports in the chat folder




---

## WhatsApp Parsing Rules

### Result detection (`_find_result`)
The parser tries four patterns in order on the **first line** of each manager message:
1. **Dash, SJ first**: `St Johns 2–1 Opponent [rest]`
2. **Dash, Opp first**: `Opponent 2–1 St Johns`
3. **Space, SJ first**: `St Johns 1 Opponent 5.` ← Ivan Paul (Sunday) format
4. **Space, Opp first**: `Opponent 3 St Johns 1.` ← Ivan Paul (Sunday) format

Prefixes like `Friendly -`, `Friendly:`, `Cup Semi:` are handled automatically — the regex finds the score embedded in the line.

### MOTM detection (`MOTM_RE`)
Patterns tried (case-insensitive) across the full result message:
- **Connector-based**: `MOM: Name`, `MOM - Name`, `MOM is Name`, `MOM goes to Name`, `MOM today is Name`, `MOM it goes to Name`, `MOM …it goes to Name`
- **No-connector**: `MOM Name` or `today's MOM Name` (name must start with capital)

### Scorer detection
Scorer lines identified by `_is_scorer_line`: comma/space-separated known player names, optionally with goal counts (e.g. `Henry 2, Keizo, Noah`). Searches the result message and next 3 messages.

### Competition type from FA PDF (`parse_results_pdf`)
FA results PDF prefix codes:
- `L` → `League`
- `F` → `Friendly`
- `Cup` → `Cup`

FA comp updates config match `comp`. More-specific names already in config (`League Cup`, `Plate Cup`, `SDFL Cup`) are preserved over the generic `Cup`.

### Squad appearances (`parse_squad_appearances`)
Squad messages (e.g. `Squad for Sunday: Conor, Ivan, Harry…`) matched to fixtures by opponent + date. Last squad before each game used. Postponed/cancelled games skipped. Used for Whites (no FA players PDF).

### Manager names (filter for result/squad posts)
| Team | WhatsApp display names |
|---|---|
| Saturday (Greens) | `SJFC Lucas Nick`, `steve`, `Steve Chez Dylans Dad` |
| Sunday (Whites) | `Mike Lowe Football St Johns`, `SJFC Ivan Paul` |

---
### Important notes
- **abandoned games** are excluded from points and played counts
- **Two players called Dylan**: Dylan H = Dylan Harper (Forward), Dylan C = Dylan Chesworth (Fwd/Mid)
- **Two players called Connor**: Connor L = Conor Lowe (played), Connor C = 0 appearances confirmed by manager
- **Elliot/Elliott** = same player (Elliot Swainston) — different spelling in squad lists
- **Walkovers** count as W, 3 pts, no goal score recorded (use "W/O")


### Sunday-only key notes
- **George Noden** = Sunday GK (different from Saturday's George GK)
- **Luke Smith** scored for Sunday team vs Winton 17 May (not in FA stats — friendly/cup only player on Sunday)
- **Moston Reds (26 Apr)** — opposition cancelled morning of game
- **Juno (10 May)** — away friendly win, score not recorded in chat
- **Trafford Titans (5 Oct)** — postponed (standing water)

---

## Team Switcher Logic


All Saturday sections have class `sat-section`. All Sunday sections have class `sun-section`. Initial state: Saturday team shown.

---

## GitHub Pages

- **Repo**: `https://github.com/Shurdsfield/stjohns`
- **Live URL**: `https://shurdsfield.github.io/stjohns`
- Push `index.html` to repo root → auto-deploys
- Token scope needed: `repo`

```bash
git add index.html
git commit -m "Describe change"
git push origin main
```

---

## Known Issues to Avoid

1. **`position:sticky` on `th`** does not work when any ancestor has `overflow:auto` or `overflow:scroll`. Do not attempt sticky table headers inside `.table-wrap` containers.
2. **Duplicate `const` declarations** in the same script scope cause a fatal parse error — entire page breaks silently.
3. **Surrogate emoji pairs** (`🧤` etc.) cannot be written directly in Python strings — use actual emoji characters (🧤 🏅 ⚽ etc.)
4. **Always test JS** before shipping using Node.js mock DOM check.
5. **Do not add `overflow-x:auto` to section containers** — breaks sticky positioning.

---
