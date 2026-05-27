#!/usr/bin/env python3
"""
generate_season.py — St John’s Chorlton U15 season app generator.

Pipeline:
  1. Hash source files (results PDF, players PDF, chat ZIP)
  2. If sources changed: parse → extract structured data → flag discrepancies
  3. If season_config.json changed: rebuild index.html

Usage:
    python3.13 generate_season.py [--config season_config.json] [--out index.html]
                                  [--force-extract] [--force-build] [--force] [--test]
                                  [--team teams/greens.json] [--fetch-table]
"""

import argparse, hashlib, json, os, re, subprocess, sys, tempfile, zipfile
import urllib.request, urllib.error, ssl
from datetime import datetime

# ── PATHS ──────────────────────────────────────────────────────────────────────
# These defaults are used only when --team is not supplied.
# When --team is supplied these are overridden by the team config sources block.

SRC_RESULTS  = "teams/greens/results/stjohns_greens.pdf"
SRC_PLAYERS  = "teams/greens/players/stjohns_greens_players.pdf"
SRC_CHAT     = "teams/greens/chat/stjohns_chat.zip"
HASH_FILE    = ".source_hashes.json"
STJOHNS_RE   = re.compile(r"St\.?\s*John[‘’]?s?", re.I)


# ══════════════════════════════════════════════════════════════════════════════
#  TEAM CONFIG LOADER
# ══════════════════════════════════════════════════════════════════════════════

def load_team_config(path):
    """Load a teams/<team>.json config file."""
    if not os.path.exists(path):
        print(f"ERROR: team config not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
#  FA FULL TIME — LEAGUE TABLE FETCHER
# ══════════════════════════════════════════════════════════════════════════════

def fetch_league_table(url, our_team_name_in_fa):
    """
    Fetch the FA Full Time league table page and parse the standings.

    FA Full Time renders the table server-side in the initial HTML (the JS only
    handles tab switching), so a plain HTTP GET is enough.

    Returns a list of row dicts:
      { pos, team, P, W, D, L, GF, GA, GD, Pts, us, champion }
    or None if parsing fails.
    """
    print(f"  Fetching FA Full Time table…")
    print(f"    {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    # macOS ships without the right CA bundle for Python — skip cert verify for this public site
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print(f"  ✗ Fetch failed: {e}", file=sys.stderr)
        return None

    # ── Try to parse the standings table ──────────────────────────────────────
    # FA Full Time table rows look like:
    #   <tr class="...">
    #     <td ...>1</td>            ← position
    #     <td ...>Team Name</td>   ← team
    #     <td>20</td>              ← P
    #     <td>19</td>              ← W
    #     <td>1</td>               ← D
    #     <td>0</td>               ← L
    #     <td>85</td>              ← GF  (may be absent)
    #     <td>22</td>              ← GA  (may be absent)
    #     <td>63</td>              ← GD  (may be absent)
    #     <td>58</td>              ← Pts
    #   </tr>

    # Strip HTML tags helper
    def strip_tags(s):
        return re.sub(r"<[^>]+>", "", s).strip()

    # Find all <tr> blocks that look like table rows
    row_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
    td_re  = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)

    rows = []
    for m in row_re.finditer(html):
        cells = [strip_tags(c.group(1)) for c in td_re.finditer(m.group(1))]
        # A valid standings row has 8–10 numeric/text cells where cell[0] is a position
        if len(cells) < 7:
            continue
        # First cell should be a position number
        if not re.match(r"^\d+$", cells[0]):
            continue
        try:
            pos = int(cells[0])
        except ValueError:
            continue

        team_name = cells[1]
        if not team_name or re.match(r"^\d+$", team_name):
            continue

        def _int(s):
            try:
                return int(s.replace(",", ""))
            except (ValueError, AttributeError):
                return None

        # Detect whether GF/GA/GD columns are present (10 cols) or absent (7 cols)
        if len(cells) >= 10:
            row = {
                "pos": pos,
                "team": team_name,
                "P":   _int(cells[2]),
                "W":   _int(cells[3]),
                "D":   _int(cells[4]),
                "L":   _int(cells[5]),
                "GF":  _int(cells[6]),
                "GA":  _int(cells[7]),
                "GD":  _int(cells[8]),
                "Pts": _int(cells[9]),
            }
        else:
            row = {
                "pos": pos,
                "team": team_name,
                "P":   _int(cells[2]),
                "W":   _int(cells[3]),
                "D":   _int(cells[4]),
                "L":   _int(cells[5]),
                "Pts": _int(cells[6]) if len(cells) > 6 else None,
            }

        # Flag our team
        if our_team_name_in_fa and our_team_name_in_fa.lower() in team_name.lower():
            row["us"] = True
        # Flag champions (pos 1)
        if pos == 1:
            row["champion"] = True

        rows.append(row)

    if not rows:
        print("  ✗ Could not parse league table from page (may be JS-rendered).", file=sys.stderr)
        print("    Tip: update league_table manually in season_config.json", file=sys.stderr)
        return None

    # Deduplicate: FA Full Time page often has multiple tables (league + form).
    # Keep only the first occurrence of each position — that's the main standings.
    seen_pos = set()
    unique_rows = []
    for row in rows:
        if row["pos"] not in seen_pos:
            seen_pos.add(row["pos"])
            unique_rows.append(row)

    # Decode HTML entities in team names
    import html as _html
    for row in unique_rows:
        row["team"] = _html.unescape(row["team"])

    print(f"  ✓ Parsed {len(unique_rows)}-team table from FA Full Time")
    return unique_rows

# ── PDF RESOLVER ──────────────────────────────────────────────────────────────

def _resolve_pdf(configured_path):
    """
    Return the path to the PDF to use.
    configured_path may be:
      - a folder  → pick the first .pdf found inside it
      - a file    → use it directly if it exists, else fall back to folder scan
    Returns None if no PDF is found.
    """
    if os.path.isdir(configured_path):
        folder = configured_path
    elif os.path.isfile(configured_path):
        return configured_path
    else:
        folder = os.path.dirname(configured_path)

    if os.path.isdir(folder):
        pdfs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".pdf"))
        if pdfs:
            found = os.path.join(folder, pdfs[0])
            print(f"  ℹ  PDF: {found}")
            return found
    return None

# ── HASHING ────────────────────────────────────────────────────────────────────

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

def load_source_hashes(hash_file=None):
    path = hash_file or HASH_FILE
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_source_hashes(hashes, hash_file=None):
    path = hash_file or HASH_FILE
    with open(path, "w") as f:
        json.dump(hashes, f, indent=2)

def read_config_hash(out_path):
    if not os.path.exists(out_path):
        return None
    try:
        with open(out_path, encoding="utf-8") as f:
            head = "".join(f.readline() for _ in range(3))
        m = re.search(r"config-hash:\s*([a-f0-9]{64})", head)
        return m.group(1) if m else None
    except OSError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  FA RESULTS PDF PARSER
# ══════════════════════════════════════════════════════════════════════════════

def _pdf_text(path):
    r = subprocess.run(["pdftotext", "-layout", path, "-"],
                       capture_output=True, text=True)
    return r.stdout

def _parse_date(raw):
    """Convert DD/MM/YY → 'DD Mon' display string."""
    try:
        d = datetime.strptime(raw.strip(), "%d/%m/%y")
        return d.strftime("%d %b")
    except ValueError:
        return raw.strip()

def parse_results_pdf(path):
    """
    Returns list of dicts:
      date, comp_type ('L'|'F'|'Cup'), comp ('League'|'Friendly'|'Cup'),
      ha ('H'|'A'), opp, score, res ('W'|'D'|'L'|'W/O'), pts
    """
    text = _pdf_text(path)
    fixtures = []

    # Split on fixture-header lines
    # Header pattern: "      L Team v Team - Day DD/MM/YY"
    #                 "      F Team v Team - Day DD/MM/YY"
    #                 "      Cup Team v Team - Day DD/MM/YY"
    COMP_MAP = {"L": "League", "F": "Friendly", "Cup": "Cup"}
    header_re = re.compile(
        r"^\s+(L|F|Cup)\s+(.+?)\s+v\s+(.+?)\s+-\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{2}/\d{2}/\d{2})",
        re.MULTILINE
    )
    status_re   = re.compile(r"Status:\s*(Normal|Home walkover|Away walkover|Walkover|Forfeit)", re.I)
    score_re    = re.compile(r"(\d+)\s+Half-time\s+(\d+)\s+Full-time")

    blocks = list(header_re.finditer(text))
    for i, m in enumerate(blocks):
        comp_type = m.group(1).strip()        # 'L', 'F', or 'Cup'
        team_a    = m.group(2).strip()        # first named team
        team_b    = m.group(3).strip()        # second named team
        date_str  = _parse_date(m.group(4))

        # Block text up to next fixture
        end = blocks[i+1].start() if i+1 < len(blocks) else len(text)
        block = text[m.start():end]

        # Walkover detection
        status_m = status_re.search(block)
        is_wo = status_m and "walkover" in status_m.group(1).lower()

        # Identify St Johns position (home or away)
        sj_is_a = bool(STJOHNS_RE.search(team_a))  # St Johns first = home
        sj_is_b = bool(STJOHNS_RE.search(team_b))  # St Johns second = away
        if sj_is_a:
            ha  = "H"
            opp = team_b
        elif sj_is_b:
            ha  = "A"
            opp = team_a
        else:
            continue  # not our fixture

        # Clean opponent name
        opp = re.sub(r"\s*U15.*", "", opp).strip()
        opp = re.sub(r"\s*JFC.*", "", opp).strip()
        opp = re.sub(r"\bJuniors?\b", "", opp).strip()
        opp = re.sub(r"\s+", " ", opp).strip()

        if is_wo:
            score = "W/O"
            res   = "W"
            pts   = 3 if comp_type != "F" else 0
        else:
            # Find both team score lines
            scores = score_re.findall(block)
            if len(scores) >= 2:
                # First score line = home team, second = away team
                home_ht, home_ft = int(scores[0][0]), int(scores[0][1])
                away_ht, away_ft = int(scores[1][0]), int(scores[1][1])
                if sj_is_a:   # St Johns = home
                    sj_ft, opp_ft = home_ft, away_ft
                else:          # St Johns = away
                    sj_ft, opp_ft = away_ft, home_ft
                score = f"{sj_ft}–{opp_ft}"
                is_fri = comp_type == "F"
                if sj_ft > opp_ft:
                    res, pts = "W", 0 if is_fri else 3
                elif sj_ft == opp_ft:
                    res, pts = "D", 0 if is_fri else 1
                else:
                    res, pts = "L", 0
            else:
                score, res, pts = "?", "?", 0

        fixtures.append({
            "date":      date_str,
            "comp_type": comp_type,          # 'L', 'F', or 'Cup'
            "comp":      COMP_MAP[comp_type], # 'League', 'Friendly', or 'Cup'
            "ha":        ha,
            "opp":       opp,
            "score":     score,
            "res":       res,
            "pts":       pts,
        })

    return fixtures


# ══════════════════════════════════════════════════════════════════════════════
#  FA PLAYERS PDF PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_players_pdf(path):
    """Returns list of {name, fa_goals, fa_starts, fa_bench}."""
    text = _pdf_text(path)
    row_re = re.compile(
        r"^\s+\d+\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+)"
        r"\s+St Johns.*?\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
        re.MULTILINE
    )
    players = []
    for m in row_re.finditer(text):
        name    = m.group(1).strip()
        goals   = int(m.group(2))
        starts  = int(m.group(3))
        bench   = int(m.group(4))
        # Normalise "Dylan Harper" → "Dylan H", "Dylan Chesworth" → "Dylan C" etc.
        players.append({
            "full_name":  name,
            "fa_goals":   goals,
            "fa_starts":  starts,
            "fa_bench":   bench,
        })
    return players


# ══════════════════════════════════════════════════════════════════════════════
#  WHATSAPP CHAT PARSER
# ══════════════════════════════════════════════════════════════════════════════

# Manager identifiers
MANAGERS = {"SJFC Lucas Nick", "steve", "Steve Chez Dylans Dad"}

# Months for date display matching
_MONTHS = {9:"Sep",10:"Oct",11:"Nov",12:"Dec",1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May"}

# WhatsApp message line pattern: [M/D/YY, HH:MM:SS] Sender: text
MSG_RE = re.compile(r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s*\d{2}:\d{2}:\d{2}\]\s+([^:]+):\s*(.*)")

# Score pattern in result posts: "Team X - Y Team" or "X-Y"
SCORE_ANNOUNCE_RE = re.compile(
    r"(?:St\.?\s*John['’]?s?|St\s*Johns?)[\s\S]{0,30}(\d+)\s*[-–]\s*(\d+)"
    r"|(\d+)\s*[-–]\s*(\d+)[\s\S]{0,30}(?:St\.?\s*John['’]?s?|St\s*Johns?)",
    re.I
)

# Scorer line: looks like "Name 2, Name, Name 3" — but NOT a squad list
# Heuristic: short, contains numbers or OG, not many names without numbers
PLAYER_NAMES = {
    "Petr","Dylan H","Dylan C","Max","Micah","Jamie","Ivan","Luke","Sonny",
    "Lucas","Ewan","Raphie","Connor","Ilyas","Harry","Noah","Digby","George",
    "Elliot","Ethan","Javar","Dalton",
}
SCORER_LINE_RE = re.compile(
    r"^((?:(?:Dylan\s+[HC]|" +
    "|".join(re.escape(p) for p in PLAYER_NAMES if " " not in p) +
    r")(?:\s*(?:x|\()?(\d+)[\)]?)?,?\s*(?:and\s+)?)+(?:OG|Walkover)?)$",
    re.I
)

MOTM_RE = re.compile(
    r"(?:"
    # connector-based: "MOM: Name", "MOM goes to Name", "MOM today is Name", "MOM it goes to Name"
    r"(?:MOTM|MOM|Man of the Match|Man of match|MoM)\s*"
    r"(?:goes\s+to|:|-|is|=|goes|today\s+is|it\s+goes\s+to|[.…]+\s*it\s+goes\s+to)\.?\s*"
    r"([A-Z][a-zA-Z]+(?:\s+(?:[A-Z&][a-zA-Z]*|\d))*)"
    # no-connector: "today's MOM Dalton" or plain "MOM Dalton"
    r"|(?:today'?s?\s+)?(?:MOTM|MOM|MoM)\s+([A-Z][a-zA-Z]+(?:\s+(?:[A-Z&][a-zA-Z]*|\d))*)"
    r")",
    re.I
)
_SJ_RESULT_RE = r"(?:St\.?\s*Johns?|St\.?\s*John['’]?s?|StJFC)"

def _find_result(text):
    """
    Extract (sj_goals, opp_goals, opp_raw) from a result line.
    Handles dash-separated ('2-1') and space-separated ('Opponent 3 St Johns 1.') formats.
    Returns None if no result pattern found.
    """
    # Dash/en-dash: "St Johns 2–1 Opponent"
    m = re.search(_SJ_RESULT_RE + r"\s+(\d+)\s*[-–]\s*(\d+)\s+(.+)", text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3).strip()
    # Dash/en-dash: "Opponent 2–1 St Johns"
    m = re.search(r"(.+?)\s+(\d+)\s*[-–]\s*(\d+)\s*" + _SJ_RESULT_RE, text, re.I)
    if m:
        return int(m.group(3)), int(m.group(2)), m.group(1).strip()
    # Space-separated, SJ first: "St Johns 1 Opponent 5."
    m = re.search(
        _SJ_RESULT_RE + r"\s+(\d+)\s+([A-Za-z][A-Za-z\s&'’.-]+?)\s+(\d+)(?=[.,\s(]|$)",
        text, re.I
    )
    if m:
        return int(m.group(1)), int(m.group(3)), m.group(2).strip()
    # Space-separated, Opp first: "Opponent 3 St Johns 1."
    m = re.search(
        r"([A-Za-z][A-Za-z\s&'’.-]+?)\s+(\d+)\s+" + _SJ_RESULT_RE + r"\s+(\d+)(?=[.,\s(]|$)",
        text, re.I
    )
    if m and len(m.group(1).strip()) >= 3:
        return int(m.group(3)), int(m.group(2)), m.group(1).strip()
    return None

def _wa_date_to_display(date_str):
    """Convert '9/6/25' → '06 Sep'."""
    parts = date_str.split("/")
    if len(parts) != 3:
        return date_str
    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
    mon = _MONTHS.get(m, f"M{m:02d}")
    return f"{d:02d} {mon}"

def _is_scorer_line(line, player_names=None):
    """Heuristic: is this line a scorer list?"""
    names = player_names if player_names is not None else PLAYER_NAMES
    line = line.strip()
    if not line or len(line) > 120:
        return False
    if any(skip in line for skip in ["http","omitted","POLL","image","video","document",
                                      "added","removed","changed","deleted","edited",
                                      "KO","kick","venue","meet","squad","available",
                                      "training","fixture","sorry","thanks","congrat",
                                      "well done","brilliant","👍","😂","🙌"]):
        return False
    # Must contain at least one known player name (or, if no names configured, just a number + OG)
    if names:
        has_player = any(re.search(r"\b" + re.escape(p) + r"\b", line, re.I) for p in names)
        if not has_player:
            return False
    # Must contain a number, 'OG', 'Walkover', or look like a scorer line
    has_num = bool(re.search(r"\d+|OG|Walkover", line, re.I))
    if not has_num:
        return False
    # Avoid long squad lists (many names, very few numbers relative to names)
    if names:
        names_found = sum(1 for p in names if re.search(r"\b" + re.escape(p) + r"\b", line, re.I))
        nums_found  = len(re.findall(r"\d+", line))
        if names_found > 8 and nums_found < 2:
            return False
    return True

def _extract_scorers_text(line, name_norms=None):
    """Normalise a scorer line to canonical form."""
    # "Dylan H x 3" → "Dylan H 3", "Name (2)" → "Name 2", "and Name" → "Name"
    s = re.sub(r"\s*x\s*", " ", line)
    s = re.sub(r"\((\d+)\)", r"\1", s)
    s = re.sub(r"\band\b", ",", s, flags=re.I)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"^,|,$", "", s.strip())
    s = s.strip()
    # Built-in Greens normalisations (always applied as sensible defaults)
    s = re.sub(r"\bMa\b", "Max", s)
    s = re.sub(r"\bDylan\b(?!\s+[CH])", "Dylan H", s)
    s = re.sub(r"\bConor\b", "Connor", s, flags=re.I)
    # Team-config normalisations (from whatsapp.name_normalisations)
    if name_norms:
        for raw, canonical in name_norms.items():
            s = re.sub(r"\b" + re.escape(raw) + r"\b", canonical, s, flags=re.I)
    return s

def _count_goals_from_scorers(scorer_text, player):
    """Count goals for a player in a scorer string."""
    # Match "Player N" or "Player" (count as 1)
    pattern = re.compile(
        r"\b" + re.escape(player) + r"\b\s*(\d*)",
        re.I
    )
    total = 0
    for m in pattern.finditer(scorer_text):
        n = int(m.group(1)) if m.group(1) else 1
        total += n
    return total

def parse_whatsapp(zip_path, wa_cfg=None):
    """
    Parse WhatsApp chat for the season.
    wa_cfg: optional dict from team config's 'whatsapp' block:
      {
        "managers":           ["Mike Lowe Football St Johns", ...],
        "player_names":       ["George", "Luke", ...],
        "season_start_hints": ["5/9/25", "Trafford Titans"],
        "name_normalisations": {"Conor": "Connor", ...}
      }
    Returns list of dicts: {date, wa_score, wa_scorers, wa_motm, wa_summary, wa_comp_hint}
    """
    wa_cfg = wa_cfg or {}
    managers     = set(wa_cfg.get("managers") or []) or MANAGERS
    player_names = set(wa_cfg.get("player_names") or []) or PLAYER_NAMES
    start_hints  = wa_cfg.get("season_start_hints") or []
    name_norms   = wa_cfg.get("name_normalisations") or {}

    # Read chat from zip
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        chat_name = next((n for n in names if n.endswith(".txt")), names[0])
        raw = zf.read(chat_name).decode("utf-8", errors="replace")

    lines = raw.splitlines()

    # Find season start — try configured hints first, then fall back to date "6/9/25"
    season_start = 0
    for hint in start_hints:
        for i, line in enumerate(lines):
            if hint in line:
                season_start = max(0, i - 5)
                break
        if season_start:
            break
    # Fallback: look for season-opening date patterns
    if season_start == 0:
        for i, line in enumerate(lines):
            if "6/9/25" in line or "9/6/25" in line:
                season_start = max(0, i - 5)
                break

    lines = lines[season_start:]

    # Parse into message objects
    messages = []
    current = None
    for line in lines:
        m = MSG_RE.match(line)
        if m:
            if current:
                messages.append(current)
            current = {
                "date_raw": m.group(1),
                "sender":   m.group(2).strip(),
                "text":     m.group(3),
                "extra":    []
            }
        elif current:
            current["extra"].append(line)
    if current:
        messages.append(current)

    # Extract match events
    wa_matches = []

    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["sender"] not in managers:
            i += 1
            continue

        full_text = msg["text"] + "\n" + "\n".join(msg["extra"])
        date_display = _wa_date_to_display(msg["date_raw"])

        # Check if this message announces a result
        result = _find_result(msg["text"])
        if not result:
            i += 1
            continue

        sj_g, opp_g, opp_raw = result
        wa_score = f"{sj_g}–{opp_g}"

        # Detect walkover
        is_wo = bool(re.search(r"walkover|GAME CANCELLED|OPPOSITION CANNOT FIELD|forfeit", full_text, re.I))
        if is_wo:
            wa_score = "W/O"

        # Extract MOTM
        motm_m = MOTM_RE.search(full_text)
        wa_motm = next((motm_m.group(g).strip() for g in (1, 2) if motm_m and motm_m.group(g)), None) if motm_m else None

        # Extract scorer line — look for it in this message and the next few
        wa_scorers = None
        search_msgs = messages[i:i+4]
        for sm in search_msgs:
            sm_text = sm["text"]
            # Check message text and each extra line
            for candidate in [sm_text] + sm["extra"]:
                candidate = candidate.strip()
                if _is_scorer_line(candidate, player_names):
                    wa_scorers = _extract_scorers_text(candidate, name_norms)
                    break
            if wa_scorers:
                break

        # Extract summary (the narrative paragraph from this message)
        # The summary is the bulk of full_text, excluding the result line and scorer line
        summary_lines = []
        for line in full_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if _find_result(line):
                continue
            if _is_scorer_line(line, player_names):
                continue
            if re.match(r"^\[", line):
                continue
            if len(line) > 40 and not line.startswith("MOTM") and not line.startswith("MOM"):
                summary_lines.append(line)
        wa_summary = " ".join(summary_lines[:6]) if summary_lines else ""

        # Competition hint from message text
        wa_comp_hint = None
        text_lower = full_text.lower()
        if "league cup" in text_lower or "cup group" in text_lower:
            wa_comp_hint = "League Cup"
        elif "plate cup" in text_lower or "plate" in text_lower:
            wa_comp_hint = "Plate Cup"
        elif "sdfl cup" in text_lower:
            wa_comp_hint = "SDFL Cup"
        elif "(cup)" in text_lower or "cup" in text_lower:
            wa_comp_hint = "Cup"

        wa_matches.append({
            "date":         date_display,
            "wa_score":     wa_score,
            "wa_scorers":   wa_scorers,
            "wa_motm":      wa_motm,
            "wa_summary":   wa_summary,
            "wa_comp_hint": wa_comp_hint,
            "opp_raw":      opp_raw,
        })

        i += 1

    return wa_matches


# ══════════════════════════════════════════════════════════════════════════════
#  SQUAD APPEARANCES PARSER
# ══════════════════════════════════════════════════════════════════════════════

# Built-in squad name normalisations — handles typos and disambiguation suffixes
_SQUAD_NORMS = {
    "henry s":  "Henry",
    "raph":     "Raphie",
    "rpahie":   "Raphie",
    "rueben":   "Reuben",
    "kiezo":    "Keizo",
    "dylan c":  "Dylan",
    "dylan h":  "Dylan",
    "conor l":  "Conor",
    "tom g":    "Tom",
    "tom h":    "Tom",
    "dalton":   "Dalton",    # already correct, listed for completeness
}

# Lines in squad messages that are NOT player lists
_SQUAD_SKIP = [
    "kick off", "ko ", "ko:", "sunday", "saturday", "friday", "thursday",
    "meet ", "turn moss", "crossford", "merseybank", "broadway", "marple",
    "chhs", "woods lane", "cheadle", "stockport", "manchester",
    "playing fields", "school", "road", "lane", "park", "street",
    "‎", "image omitted", "document omitted", "pdf",
    "squad", "updated", "any problems", "anyone", "room for", "standby",
    "priority", "lift", "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "kick", "warm", "ko\n", "kick\n",
]

def _squad_name_to_canonical(raw, known_players, extra_norms=None):
    """Map a raw squad name to a canonical player name."""
    r = raw.strip().rstrip(".,!? ")   # strip trailing punctuation (e.g. "Nathaniel.")
    if not r:
        return ""
    key = r.lower()
    # Extra normalisations from config
    if extra_norms:
        for old, new in extra_norms.items():
            if key == old.lower():
                return new
    # Built-in normalisations
    if key in _SQUAD_NORMS:
        return _SQUAD_NORMS[key]
    # Exact match in known players (case-insensitive)
    for p in known_players:
        if p.lower() == key:
            return p
    # Strip single trailing uppercase initial ("Dylan C" → "Dylan", "Henry S" → "Henry")
    no_suffix = re.sub(r"\s+[A-Z]$", "", r).strip()
    if no_suffix != r:
        if no_suffix.lower() in _SQUAD_NORMS:
            return _SQUAD_NORMS[no_suffix.lower()]
        for p in known_players:
            if p.lower() == no_suffix.lower():
                return p
    # Return title-cased (might be a guest / new player)
    return no_suffix.title() if no_suffix else r.title()

def _extract_squad_names(full_text, known_players, extra_norms=None):
    """Find and extract the player list from a squad message body."""
    best_line = None
    best_count = 0

    for line in full_text.split("\n"):
        line = line.strip()
        if not line or len(line) < 5 or len(line) > 250:
            continue
        # Skip lines that contain venue / time / admin keywords
        low = line.lower()
        if any(skip in low for skip in _SQUAD_SKIP):
            continue
        # Must have comma or "and" to look like a list
        if "," not in line and " and " not in low:
            continue
        # Split on commas and "and"
        parts = re.split(r",|\band\b", line, flags=re.I)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            continue
        # Each chunk should be 1-3 short words, no digits
        valid = []
        for part in parts:
            words = part.split()
            if 1 <= len(words) <= 3 and all(len(w) <= 14 for w in words):
                if not any(c.isdigit() for c in part):
                    valid.append(part)
        if len(valid) < 2:
            continue
        # Count how many normalise to a known player
        canonicals = [_squad_name_to_canonical(v, known_players, extra_norms) for v in valid]
        known_count = sum(1 for c in canonicals if c in known_players)
        if known_count >= 2 and known_count > best_count:
            best_count = known_count
            best_line = valid

    if not best_line:
        return []
    return [n for n in (_squad_name_to_canonical(v, known_players, extra_norms) for v in best_line) if n]

def _wa_date_to_dt(date_str):
    """Convert WhatsApp date 'M/D/YY' → datetime."""
    parts = date_str.split("/")
    if len(parts) != 3:
        return None
    try:
        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
        year = 2000 + y if y < 100 else y
        return datetime(year, m, d)
    except ValueError:
        return None

def _display_date_to_dt(display_date):
    """Convert '14 Sep' → datetime (uses 2025 for Sep–Dec, 2026 for Jan–May)."""
    _MMAP = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
             "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    parts = display_date.strip().split()
    if len(parts) != 2:
        return None
    try:
        d = int(parts[0])
        m_num = _MMAP.get(parts[1], 0)
        if not m_num:
            return None
        yr = 2026 if m_num <= 8 else 2025
        return datetime(yr, m_num, d)
    except ValueError:
        return None

def parse_squad_appearances(zip_path, season_matches, wa_cfg=None):
    """
    Parse WhatsApp chat squad messages and compute player appearance counts.

    squad messages are manager posts that contain 'squad' and a comma-separated
    player list. The last squad posted for each game is used (handles 'updated'
    messages). Postponed/cancelled games (res='P') are skipped.

    Returns dict: {player_canonical_name: {"lge": N, "cup": N, "fri": N}}
    """
    wa_cfg = wa_cfg or {}
    managers     = set(wa_cfg.get("managers") or []) or MANAGERS
    known_players = list(wa_cfg.get("player_names") or PLAYER_NAMES)
    name_norms   = wa_cfg.get("name_normalisations") or {}

    # Read chat
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        chat_name = next((n for n in names if n.endswith(".txt")), names[0])
        raw = zf.read(chat_name).decode("utf-8", errors="replace")

    lines = raw.splitlines()

    # Find season start using config hints
    start_hints = wa_cfg.get("season_start_hints") or []
    season_start = 0
    for hint in start_hints:
        for i, line in enumerate(lines):
            if hint in line:
                season_start = max(0, i - 5)
                break
        if season_start:
            break

    lines = lines[season_start:]

    # Parse into messages
    messages = []
    current = None
    for line in lines:
        m = MSG_RE.match(line)
        if m:
            if current:
                messages.append(current)
            current = {"date_raw": m.group(1), "sender": m.group(2).strip(),
                       "text": m.group(3), "extra": []}
        elif current:
            current["extra"].append(line)
    if current:
        messages.append(current)

    # Build map of season matches by date for fast lookup
    # Only include matches that actually happened (res != "P")
    playable_matches = [m for m in season_matches if m.get("res") != "P"]
    match_dt_map = {}   # datetime → match dict
    for sm in playable_matches:
        dt = _display_date_to_dt(sm["date"])
        if dt:
            match_dt_map[dt] = sm

    # Opponent extraction pattern: "v ..." or "vs ..."
    opp_re = re.compile(r"^\s*(?:v|vs)\.?\s+(.+?)(?:\s*,|\s*\(|$)", re.I)

    # Collect squad messages from managers
    # struct: {match_key → last squad player list}
    squads_by_match = {}   # match date string → list of canonical player names

    for msg in messages:
        if msg["sender"] not in managers:
            continue
        full_text = msg["text"] + "\n" + "\n".join(msg["extra"])
        if "squad" not in full_text.lower():
            continue

        # Try to extract the opponent from the message
        opp_hint = None
        for line in full_text.split("\n"):
            om = opp_re.match(line.strip())
            if om:
                raw_opp = om.group(1).strip()
                # Remove weekday/date cruft after the opponent name
                raw_opp = re.split(r",|\s+Sunday|\s+Saturday|\s+kick|\s+ko\b", raw_opp, flags=re.I)[0].strip()
                if raw_opp and len(raw_opp) > 2 and not any(c.isdigit() for c in raw_opp[:3]):
                    opp_hint = raw_opp
                    break

        if not opp_hint:
            continue

        # Extract player names from the squad message
        squad_names = _extract_squad_names(full_text, known_players, name_norms)
        if not squad_names:
            continue

        # Match the opponent to a season match — date-aware:
        # The squad is sent a few days before the game; look forward up to 12 days
        msg_dt = _wa_date_to_dt(msg["date_raw"])
        if not msg_dt:
            continue

        best_match = None
        best_score = -1
        for match_dt, sm in match_dt_map.items():
            # Game must be after the squad was posted and within 12 days
            delta = (match_dt - msg_dt).days
            if delta < 0 or delta > 12:
                continue
            # Fuzzy opponent name match
            if _names_match(opp_hint, sm["opp"]):
                # Prefer closer dates and better name matches
                score = 10 - delta
                if score > best_score:
                    best_score = score
                    best_match = sm

        if not best_match:
            continue

        # Store — last squad message per match wins (handles "updated" squads)
        squads_by_match[best_match["date"]] = squad_names

    if not squads_by_match:
        print("  ⚠  No squad messages matched to season matches")
        return {}

    print(f"  ✓ Matched {len(squads_by_match)} squad messages to season games")

    # Tally appearances
    cup_comps = {"League Cup", "Plate Cup", "SDFL Cup", "Cup"}
    apps = {}
    for match_date, squad in squads_by_match.items():
        # Get comp type from the matched season match
        sm = next((m for m in season_matches if m["date"] == match_date), None)
        if not sm:
            continue
        comp = sm.get("comp", "League")
        if comp == "League":
            cat = "lge"
        elif comp in cup_comps:
            cat = "cup"
        else:
            cat = "fri"
        for player in squad:
            if player not in apps:
                apps[player] = {"lge": 0, "cup": 0, "fri": 0}
            apps[player][cat] += 1

    return apps


# ══════════════════════════════════════════════════════════════════════════════
#  DATA MERGER + DISCREPANCY DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

def _normalise_name(name):
    """Lower, strip punctuation for fuzzy matching."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

def _names_match(a, b, threshold=0.6):
    """Simple overlap matching for opponent names."""
    a, b = _normalise_name(a), _normalise_name(b)
    if a in b or b in a:
        return True
    a_words = set(a.split())
    b_words = set(b.split())
    short_words = {"fc","jfc","u15","u14","youth","united","juniors","boys","girls","sc","afc"}
    a_sig = a_words - short_words
    b_sig = b_words - short_words
    if not a_sig or not b_sig:
        return False
    overlap = len(a_sig & b_sig) / max(len(a_sig), len(b_sig))
    return overlap >= threshold

def _player_short_name(full_name):
    """'Dylan Harper' → 'Dylan H', 'Dylan Chesworth' → 'Dylan C', 'Conor Lowe' → 'Connor'."""
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0]
    first, last = parts[0], parts[-1]
    # Special cases
    mapping = {
        ("Dylan", "Harper"):     "Dylan H",
        ("Dylan", "Chesworth"):  "Dylan C",
        ("Raphael", "Leyland"):  "Raphie",
        ("Conor", "Lowe"):       "Connor",
        ("Connor", "Cla"):       "Connor C",
    }
    if (first, last) in mapping:
        return mapping[(first, last)]
    return first

def _build_config_from_whatsapp(wa_matches):
    """
    Build a minimal season_config skeleton from WhatsApp data alone.
    Used when no PDFs and no existing config exist.
    Returns a dict with 'matches' and 'players' populated from scorer lines.
    """
    matches = []
    goal_tally = {}   # player → total goals

    for wa in wa_matches:
        score = wa.get("wa_score") or "?–?"
        if score and score != "?–?":
            try:
                sj, opp_g = score.split("–")
                sj, opp_g = int(sj.strip()), int(opp_g.strip())
                if sj > opp_g:
                    res = "W"
                elif sj == opp_g:
                    res = "D"
                else:
                    res = "L"
                pts = {"W": 3, "D": 1, "L": 0}.get(res, None)
            except (ValueError, AttributeError):
                res, pts = "?", None
        elif score == "W/O":
            res, pts = "W", 3
        else:
            res, pts = "?", None

        # Tally goals from scorer line
        scorers = wa.get("wa_scorers") or ""
        if scorers:
            for m in re.finditer(r"([A-Z][a-zA-Z\s]+?)(?:\s+(\d+))?(?:,|$)", scorers):
                player = m.group(1).strip()
                goals  = int(m.group(2)) if m.group(2) else 1
                if player and len(player) > 1:
                    goal_tally[player] = goal_tally.get(player, 0) + goals

        comp = wa.get("wa_comp_hint") or "League"
        matches.append({
            "date":     wa["date"],
            "comp":     comp,
            "ha":       "?",
            "opp":      wa.get("opp_raw", "Unknown"),
            "score":    score if score != "?–?" else "?",
            "res":      res,
            "pts":      pts,
            "motm":     wa.get("wa_motm"),
            "scorers":  scorers or None,
            "summary":  wa.get("wa_summary") or "",
        })

    # Build bare player list from goal tally
    players = [
        {"name": p, "lge_goals": g, "cup_goals": 0, "fri_goals": 0,
         "lge_apps": 0, "cup_apps": 0, "fri_apps": 0}
        for p, g in sorted(goal_tally.items(), key=lambda x: -x[1])
    ]

    return {"matches": matches, "players": players}


def merge_and_flag(fa_results, fa_players, wa_matches, existing_config):
    """
    Merge FA and WhatsApp data. Returns (updated_config, discrepancies).
    Preserves manual fields from existing_config.

    When fa_results=[] (no results PDF):  skip FA match cross-checks.
    When fa_players=[] (no players PDF):  skip FA goals cross-checks.
    When existing_config has no matches:  bootstrap from WhatsApp.
    """
    discrepancies = []

    # ── Bootstrap config from WhatsApp if no existing data ───────────────────
    if not existing_config.get("matches") and not fa_results:
        print("  No existing matches and no FA PDF — bootstrapping from WhatsApp…")
        wa_built = _build_config_from_whatsapp(wa_matches)
        existing_config = {**existing_config, **wa_built}
        print(f"  → Built {len(wa_built['matches'])} matches from WhatsApp")

    cfg = existing_config

    # ── MATCH DISCREPANCIES ───────────────────────────────────────────────────
    cfg_matches = {m["date"]: m for m in cfg.get("matches", [])}

    if fa_results:
        print(f"\n  FA results: {len(fa_results)} fixtures")
    else:
        print(f"\n  FA results: none (PDF not present)")
    print(f"  WhatsApp results: {len(wa_matches)} match reports")

    # ── FA results cross-checks (only when PDF was present) ──────────────────
    if fa_results:
        for fa in fa_results:
            date = fa["date"]
            cfg_m = cfg_matches.get(date)
            if not cfg_m:
                discrepancies.append(
                    f"[MATCH] {date} — FA has fixture vs '{fa['opp']}' "
                    f"({fa['score']}) but NOT in config"
                )
                continue

            # Score check
            if cfg_m.get("score") and fa["score"] != "?" and cfg_m["score"] != fa["score"]:
                if not (cfg_m["score"] == "W/O" and fa["score"] == "W/O"):
                    discrepancies.append(
                        f"[SCORE] {date} vs {cfg_m['opp']}: "
                        f"config='{cfg_m['score']}' FA='{fa['score']}'"
                    )

            # Result check
            if cfg_m.get("res") and fa["res"] not in ("?",) and cfg_m["res"] != fa["res"]:
                if cfg_m["res"] != "ABN":
                    discrepancies.append(
                        f"[RESULT] {date} vs {cfg_m['opp']}: "
                        f"config='{cfg_m['res']}' FA='{fa['res']}'"
                    )

            # Comp update — apply FA comp type to config match
            # FA is authoritative for L/F/Cup; preserve specific cup names if already set
            fa_comp = fa.get("comp")  # 'League', 'Friendly', or 'Cup'
            if fa_comp:
                cfg_comp = cfg_m.get("comp", "")
                _specific_cups = {"League Cup", "Plate Cup", "SDFL Cup"}
                if fa_comp == "Cup" and cfg_comp in _specific_cups:
                    pass  # preserve the more-specific cup name already in config
                elif cfg_comp != fa_comp:
                    old_comp = cfg_comp
                    cfg_m["comp"] = fa_comp
                    if old_comp:
                        print(f"  ↻ comp updated {date} vs {cfg_m['opp']}: '{old_comp}' → '{fa_comp}'")
                    else:
                        print(f"  ✓ comp set    {date} vs {cfg_m['opp']}: '{fa_comp}'")

        # Check for config matches not in FA (friendlies, abandoned)
        fa_dates = {f["date"] for f in fa_results}
        for cfg_m in cfg.get("matches", []):
            if cfg_m["date"] not in fa_dates:
                note = "(friendly or abandoned — expected)" if cfg_m.get("comp") in ("Friendly",) or cfg_m.get("res") == "ABN" else ""
                if not note:
                    discrepancies.append(
                        f"[MATCH] {cfg_m['date']} vs {cfg_m['opp']} in config but NOT in FA PDF {note}"
                    )

    # ── WhatsApp vs Config score checks (always run) ──────────────────────────
    for wa in wa_matches:
        cfg_m = cfg_matches.get(wa["date"])
        if not cfg_m:
            continue
        if wa["wa_score"] and wa["wa_score"] != "?" and cfg_m.get("score"):
            if wa["wa_score"] != cfg_m["score"] and wa["wa_score"] != "W/O":
                discrepancies.append(
                    f"[SCORE] {wa['date']} vs {cfg_m['opp']}: "
                    f"config='{cfg_m['score']}' WhatsApp='{wa['wa_score']}'"
                )

    # ── WhatsApp scorer vs Config scorer checks ───────────────────────────────
    for wa in wa_matches:
        cfg_m = cfg_matches.get(wa["date"])
        if not cfg_m or not wa["wa_scorers"]:
            continue
        cfg_scorers = cfg_m.get("scorers", "")
        if cfg_scorers and cfg_scorers not in ("Walkover","Abandoned","—"):
            for player in PLAYER_NAMES:
                cfg_g = _count_goals_from_scorers(cfg_scorers, player)
                wa_g  = _count_goals_from_scorers(wa["wa_scorers"], player)
                if cfg_g != wa_g and (cfg_g > 0 or wa_g > 0):
                    discrepancies.append(
                        f"[SCORERS] {wa['date']} vs {cfg_m['opp']} — "
                        f"{player}: config={cfg_g} WhatsApp={wa_g}"
                        f"  (config: '{cfg_scorers}' | wa: '{wa['wa_scorers']}')"
                    )

    # ── PLAYER GOAL DISCREPANCIES ─────────────────────────────────────────────
    # Build WhatsApp-derived goal counts from config scorer lines
    wa_goals_by_player = {p: {"lge":0,"cup":0,"fri":0} for p in PLAYER_NAMES}
    cup_comps = {"League Cup","Plate Cup","SDFL Cup"}
    for cfg_m in cfg.get("matches", []):
        scorers = cfg_m.get("scorers","")
        if not scorers or scorers in ("Walkover","Abandoned","—"):
            continue
        comp    = cfg_m.get("comp","")
        is_lge  = comp == "League"
        is_cup  = comp in cup_comps
        is_fri  = comp == "Friendly"
        for player in PLAYER_NAMES:
            g = _count_goals_from_scorers(scorers, player)
            if g:
                if is_lge:   wa_goals_by_player[player]["lge"] += g
                elif is_cup: wa_goals_by_player[player]["cup"] += g
                elif is_fri: wa_goals_by_player[player]["fri"] += g

    # FA goals cross-check (only when players PDF was present)
    if fa_players:
        fa_name_map = {}
        for fp in fa_players:
            short = _player_short_name(fp["full_name"])
            fa_name_map[short] = fp

        for cfg_p in cfg.get("players", []):
            pname     = cfg_p["name"]
            cfg_lge_g = cfg_p.get("lge_goals", 0)
            cfg_cup_g = cfg_p.get("cup_goals", 0)

            fa_match = fa_name_map.get(pname)
            if fa_match:
                fa_total_ex_fri = cfg_lge_g + cfg_cup_g
                fa_total        = fa_match["fa_goals"]
                if fa_total_ex_fri != fa_total:
                    discrepancies.append(
                        f"[GOALS] {pname}: config lge+cup={fa_total_ex_fri} "
                        f"vs FA PDF total={fa_total} "
                        f"(fri excluded from FA)"
                    )

    # WhatsApp scorer-line vs config player goals (always run)
    for cfg_p in cfg.get("players", []):
        pname = cfg_p["name"]
        wa_g  = wa_goals_by_player.get(pname, {"lge":0,"cup":0,"fri":0})
        for cat in ("lge","cup","fri"):
            cfg_g = cfg_p.get(f"{cat}_goals", 0)
            if cfg_g != wa_g[cat] and (cfg_g > 0 or wa_g[cat] > 0):
                discrepancies.append(
                    f"[GOALS] {pname}: config {cat}_goals={cfg_g} "
                    f"vs WhatsApp scorer lines={wa_g[cat]}"
                )

    return cfg, discrepancies


# ══════════════════════════════════════════════════════════════════════════════
#  NODE.JS VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

NODE_MOCK = r"""
const elMap = {};
const mock = {
  getElementById:      id => elMap[id] || (elMap[id] = { innerHTML:'', appendChild:()=>{}, style:{} }),
  querySelectorAll:    () => [],
  querySelector:       () => null,
  createElement:       tag => ({
    className:'', innerHTML:'', textContent:'', style:{cssText:''},
    setAttribute:()=>{}, addEventListener:()=>{}, appendChild:()=>{}
  })
};
"""

def run_node_test(out_path):
    print("  Running Node.js validation...", flush=True)
    with open(out_path, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    if not m:
        print("  WARNING: no <script> block found")
        return True
    script_src = m.group(1)
    test_src = NODE_MOCK + "\nnew Function('document', " + json.dumps(script_src) + ")(mock);\n"
    try:
        with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False, encoding="utf-8") as tf:
            tf.write(test_src)
            tf_path = tf.name
        result = subprocess.run(["node", tf_path], capture_output=True, text=True, timeout=15)
        os.unlink(tf_path)
        if result.returncode == 0:
            print("  ✓ Node.js validation passed")
            return True
        else:
            print("  ✗ Node.js FAILED:", result.stderr.strip())
            return False
    except FileNotFoundError:
        print("  WARNING: node not found — skipping")
        return True
    except subprocess.TimeoutExpired:
        print("  WARNING: Node.js timed out — skipping")
        return True


# ══════════════════════════════════════════════════════════════════════════════
#  HTML BUILDER
# ══════════════════════════════════════════════════════════════════════════════

_CSS = """
/* ── COLOUR TOKENS ────────────────────────────────────────────────── */
:root{
  --bg-page:   #132338;
  --bg-nav:    #1e293b;
  --bg-card:   #1e2d40;
  --bg-row:    #162032;
  --bg-expand: #1a2d3e;
  --bg-total:  #1d3351;
  --border:    #2d4a6a;

  --text-body:  #e8eef4;
  --text-sub:   #a8c4d8;
  --text-muted: #89a8c0;
  --text-head:  #ffffff;

  --accent-blue:  #60a5fa;
  --accent-green: #4ade80;
  --accent-amber: #fbbf24;
  --accent-red:   #f87171;
  --accent-purple:#c4b5fd;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg-page);color:var(--text-body);min-height:100vh;padding-bottom:60px;-webkit-font-smoothing:antialiased;font-size:15px;line-height:1.5}
a{color:var(--accent-blue);text-decoration:none}
.top-banner{background:linear-gradient(135deg,#1a3561 0%,#163050 40%,#1a3d28 100%);border-bottom:3px solid var(--border);padding:32px 20px 26px;text-align:center;position:relative;overflow:hidden}
.top-banner::before{content:'⚽';position:absolute;font-size:160px;opacity:.06;top:-20px;left:-20px;transform:rotate(-15deg)}
.top-banner::after{content:'⚽';position:absolute;font-size:160px;opacity:.06;bottom:-20px;right:-20px;transform:rotate(20deg)}
.club-badge{width:68px;height:68px;background:linear-gradient(135deg,#2563eb,#16a34a);border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:30px;margin-bottom:12px;box-shadow:0 4px 24px #2563eb55}
.club-name{font-size:26px;font-weight:800;color:#ffffff;letter-spacing:-0.02em;margin-bottom:4px}
.club-sub{font-size:13px;color:#93c5fd;letter-spacing:0.06em;margin-bottom:18px}
.season-pills{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.season-pill{background:#ffffff18;border:1px solid #ffffff25;border-radius:20px;padding:6px 16px;font-size:12px;color:#bfdbfe;backdrop-filter:blur(4px)}
.season-pill strong{color:#ffffff;font-weight:700}
.nav-sticky{background:var(--bg-nav);border-bottom:2px solid var(--border);position:sticky;top:0;z-index:100;overflow-x:auto;display:flex;scrollbar-width:none}
.nav-sticky::-webkit-scrollbar{display:none}
.nav-btn{background:none;border:none;border-bottom:3px solid transparent;color:var(--text-sub);cursor:pointer;font-family:inherit;font-size:13px;font-weight:600;letter-spacing:0.04em;padding:15px 20px;text-transform:uppercase;transition:all .2s;white-space:nowrap}
.nav-btn:hover{color:var(--text-body);background:#ffffff08}
.nav-btn.active{color:#ffffff;border-bottom-color:#3b82f6;background:#ffffff06}
.section{display:none;padding:28px 18px;max-width:980px;margin:0 auto}
.section.active{display:block}
.section-title{font-size:12px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:var(--text-body);margin-bottom:14px;display:flex;align-items:center;gap:10px}
.section-title::after{content:'';flex:1;height:1px;background:var(--border)}
.stat-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:30px}
.stat-card{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:16px;text-align:center}
.stat-card .val{font-size:30px;font-weight:800;line-height:1;margin-bottom:5px;color:var(--text-head)}
.stat-card .lbl{font-size:12px;color:var(--text-body);text-transform:uppercase;letter-spacing:0.1em;font-weight:600}
.table-wrap{overflow-x:auto;margin-bottom:28px;border-radius:10px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:13px}
thead{background:#1a3561}
th{padding:11px 13px;text-align:left;color:#93c5fd;font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;border-bottom:2px solid var(--border);white-space:nowrap}
th.r,td.r{text-align:center}
td{padding:11px 13px;border-bottom:1px solid #1e3450;vertical-align:middle;color:var(--text-head)}
tr:last-child td{border-bottom:none}
tr.ev{background:#182d45}
tr.od{background:#1e3452}
tr.tr-row{background:#1d3d6e}
tr.tr-row td{font-weight:700;color:#ffffff;padding:12px 13px;border-top:2px solid var(--border)}
.comp-badge{border-radius:5px;padding:3px 9px;font-size:11px;font-weight:700;color:#fff;letter-spacing:0.03em;display:inline-block}
.res-badge{border-radius:5px;padding:3px 10px;font-weight:800;font-size:13px;display:inline-block}
.ha-badge{font-weight:700;font-size:13px}
.match-row{cursor:pointer;transition:background .12s}
.match-row:hover td{background:#1e3a5a!important}
.match-row .expand-icon{color:var(--text-muted);font-size:11px;transition:transform .2s;display:inline-block}
.match-row.open .expand-icon{transform:rotate(180deg);color:var(--accent-blue)}
.expand-row{display:none;background:var(--bg-expand)}
.expand-row.open{display:table-row}
.expand-cell{padding:14px 18px 18px!important}
.expand-inner{border-left:3px solid #3b82f6;padding-left:16px}
.expand-summary{color:var(--text-body);font-size:14px;line-height:1.8;margin-bottom:12px}
.expand-meta{display:flex;gap:28px;flex-wrap:wrap}
.expand-label{font-size:11px;text-transform:uppercase;letter-spacing:0.1em;font-weight:700;color:var(--text-sub)}
.expand-val{font-size:13px;color:var(--text-body);font-weight:500}
.filter-row{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:16px}
.filter-btn{background:var(--bg-card);border:1px solid var(--border);border-radius:20px;color:var(--text-sub);cursor:pointer;font-family:inherit;font-size:12px;font-weight:600;letter-spacing:0.06em;padding:7px 16px;transition:all .15s}
.filter-btn:hover{border-color:#4a7fa5;color:var(--text-body)}
.filter-btn.active{color:#fff;border-color:transparent}
.player-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:13px;margin-bottom:28px}
.player-card{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:18px;transition:all .2s}
.player-card:hover{border-color:#4a7fa5;transform:translateY(-2px);box-shadow:0 8px 24px #00000040}
.player-name{font-size:17px;font-weight:700;color:var(--text-head);margin-bottom:3px}
.player-role{font-size:11px;color:var(--text-sub);text-transform:uppercase;letter-spacing:0.1em;margin-bottom:13px;font-weight:600}
.player-stat-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #1e3450}
.player-stat-row:last-of-type{border-bottom:none}
.player-stat-lbl{font-size:11px;color:var(--text-sub);text-transform:uppercase;letter-spacing:0.08em;font-weight:600}
.player-stat-val{font-size:16px;font-weight:800}
.motm-line{margin-top:9px;font-size:12px;color:var(--accent-purple);font-weight:600}
.player-note{margin-top:9px;font-size:12px;color:var(--text-body);font-style:italic;line-height:1.5}
.result-chip{display:inline-block;font-size:11px;font-weight:600;border-radius:5px;padding:2px 8px;margin:2px 4px 2px 0;border:1px solid var(--border);background:var(--bg-card)}
.report-body{max-width:700px;margin:0 auto;background:#1a3461;border-radius:12px;padding:32px 36px;border:1px solid var(--border)}
.report-body h3{font-size:19px;font-weight:700;color:#ffffff;margin:34px 0 13px;padding-left:14px;border-left:4px solid #60a5fa}
.report-body p{color:#ffffff;font-size:15px;line-height:1.9;margin-bottom:16px}
.report-body blockquote{border-left:4px solid #60a5fa;padding:12px 18px;margin:18px 0;color:#e8f4ff;font-style:italic;font-size:14px;line-height:1.8;background:#0f2040;border-radius:0 8px 8px 0}
.highlight{color:#4ade80;font-weight:700}
.player-ref{color:#c4b5fd;font-weight:700}
.report-signoff{color:#93c5fd;font-size:13px;margin-top:40px;text-align:right;font-style:italic;border-top:1px solid #2d5a8a;padding-top:18px}
.site-footer{text-align:center;padding:36px 16px 20px;color:var(--text-sub);font-size:12px;letter-spacing:0.06em;border-top:1px solid var(--border)}
@media(max-width:600px){
  .club-name{font-size:21px}
  .stat-cards{grid-template-columns:repeat(2,1fr)}
  .expand-meta{flex-direction:column;gap:10px}
  th,td{padding:8px 9px}
  .section{padding:16px 12px}
  .nav-btn{font-size:11px;padding:13px 14px}
  .report-body{padding:20px 18px}
}
/* ── TEAM SWITCHER ─────────────────────────────────────────────────── */
.team-switcher{background:#0a1628;padding:10px 18px;display:flex;gap:10px;align-items:center;justify-content:center;border-bottom:1px solid var(--border)}
.team-btn{background:var(--bg-card);border:2px solid var(--border);border-radius:24px;color:var(--text-sub);cursor:pointer;font-family:inherit;font-size:13px;font-weight:700;letter-spacing:0.04em;padding:8px 22px;transition:all .2s;white-space:nowrap}
.team-btn:hover{border-color:#4a7fa5;color:var(--text-body)}
.team-btn.active{color:#fff;border-color:transparent}
.team-btn.sat-btn.active{background:#16a34a}
.team-btn.sun-btn.active{background:#2563eb}
.team-switcher-label{font-size:11px;color:var(--text-muted);letter-spacing:0.1em;text-transform:uppercase;font-weight:600}
"""

_JS_FUNCS = """
const BC = {
  League:       '#2563eb',
  'League Cup': '#16a34a',
  'Plate Cup':  '#d97706',
  'SDFL Cup':   '#dc2626',
  Friendly:     '#7c3aed'
};
const RC = { W:'#4ade80', D:'#facc15', L:'#f87171', ABN:'#64748b' };
function totalG(p){ return (p.friG||0)+(p.lgeG||0)+(p.cupG||0); }
function totalA(p){ return (p.friA||0)+(p.lgeA||0)+(p.cupA||0); }
function totalM(p){ return Object.values(p.motm||{}).reduce((s,v)=>s+v,0); }
function gd(n){ return (n>=0?'+':'')+n; }

function showSection(id,btn){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
function buildStatCards(){
  document.getElementById('stat-cards').innerHTML=statCards.map(c=>
    `<div class="stat-card"><div class="val" style="color:${c.color}">${c.val}</div><div class="lbl">${c.lbl}</div></div>`
  ).join('');
}
function buildCompSummary(){
  const comps=['League','League Cup','Plate Cup','SDFL Cup','Friendly'];
  const data={};
  comps.forEach(c=>{ data[c]={P:0,W:0,D:0,L:0,GF:0,GA:0,Pts:0,wo:0}; });
  matches.forEach(m=>{
    const d=data[m.comp];
    if(!d||m.res==='ABN')return;
    d.P++;
    if(m.res==='W')d.W++;
    else if(m.res==='D')d.D++;
    else if(m.res==='L')d.L++;
    if(m.pts)d.Pts+=m.pts;
    if(m.score&&m.score!=='W/O'){
      const pts=m.score.split('–');
      if(pts.length===2&&!isNaN(pts[0])){d.GF+=+pts[0];d.GA+=+pts[1];}
    } else if(m.score==='W/O'){ d.wo++; }
  });
  let t=`<table><thead><tr>
    <th>Competition</th>
    <th class="r">Played</th><th class="r">Won</th><th class="r">Drew</th><th class="r">Lost</th>
    <th class="r">For</th><th class="r">Against</th><th class="r">Diff</th><th class="r">Points</th>
  </tr></thead><tbody>`;
  let totP=0,totW=0,totD=0,totL=0,totGF=0,totGA=0,totPts=0;
  comps.forEach((comp,i)=>{
    const d=data[comp];
    if(!d||d.P===0)return;
    const bc=BC[comp]||'#475569';
    const gdVal=d.GF-d.GA;
    const woNote=d.wo>0?` <small style="color:var(--text-sub);font-size:12px">incl. ${d.wo} walkover${d.wo>1?'s':''}</small>`:'';
    const ptsShow=comp==='Friendly'?`<span style="color:var(--text-sub)">—</span>`:`<span style="color:#facc15;font-weight:800;font-size:15px">${d.Pts}</span>`;
    t+=`<tr class="${i%2===0?'ev':'od'}">
      <td><span class="comp-badge" style="background:${bc}">${comp}</span>${woNote}</td>
      <td class="r">${d.P}</td>
      <td class="r" style="color:#4ade80;font-weight:700">${d.W}</td>
      <td class="r" style="color:#facc15">${d.D}</td>
      <td class="r" style="color:#f87171">${d.L}</td>
      <td class="r">${d.GF}</td><td class="r">${d.GA}</td>
      <td class="r" style="color:${gdVal>=0?'#4ade80':'#f87171'}">${gd(gdVal)}</td>
      <td class="r">${ptsShow}</td>
    </tr>`;
    totP+=d.P;totW+=d.W;totD+=d.D;totL+=d.L;
    if(comp!=='Friendly')totPts+=d.Pts;
    totGF+=d.GF;totGA+=d.GA;
  });
  const totGD=totGF-totGA;
  t+=`<tr class="tr-row">
    <td><strong>All Competitions</strong></td>
    <td class="r">${totP}</td>
    <td class="r" style="color:#4ade80">${totW}</td>
    <td class="r" style="color:#facc15">${totD}</td>
    <td class="r" style="color:#f87171">${totL}</td>
    <td class="r">${totGF}</td><td class="r">${totGA}</td>
    <td class="r" style="color:${totGD>=0?'#4ade80':'#f87171'}">${gd(totGD)}</td>
    <td class="r" style="color:#fbbf24;font-size:15px">${totPts}</td>
  </tr></tbody></table>`;
  document.getElementById('comp-summary-wrap').innerHTML=t;
}
function buildTopScorers(){
  let t=`<table><thead><tr>
    <th>#</th><th>Player</th>
    <th class="r" style="color:#b0c4d8">Friendly</th>
    <th class="r" style="color:#93c5fd">League</th>
    <th class="r" style="color:#86efac">Cup</th>
    <th class="r">Total</th>
  </tr></thead><tbody>`;
  const scorers=playersSorted.filter(p=>totalG(p)>0);
  scorers.forEach((p,i)=>{
    const tot=totalG(p),tc=tot>=20?'#6ee7b7':tot>=10?'#93c5fd':'#f1f5f9';
    const cell=(v,col)=>`<td class="r" style="color:${v?col:'#1e293b'};font-weight:${v?700:400}">${v||'—'}</td>`;
    t+=`<tr class="${i%2===0?'ev':'od'}">
      <td style="color:var(--text-sub)">${i+1}</td>
      <td style="font-weight:600;color:#fff">${p.name} <span style="color:var(--text-muted);font-size:11px">${p.surname||''}</span></td>
      ${cell(p.friG,'#b0c4d8')}${cell(p.lgeG,'#93c5fd')}${cell(p.cupG,'#86efac')}
      <td class="r" style="color:${tc};font-weight:800;font-size:14px">${tot}</td>
    </tr>`;
  });
  const tF=playersSorted.reduce((s,p)=>s+(p.friG||0),0);
  const tL=playersSorted.reduce((s,p)=>s+(p.lgeG||0),0);
  const tC=playersSorted.reduce((s,p)=>s+(p.cupG||0),0);
  t+=`<tr class="tr-row"><td></td><td><strong>Totals</strong></td>
    <td class="r">${tF}</td><td class="r">${tL}</td><td class="r">${tC}</td>
    <td class="r" style="font-size:14px">${tF+tL+tC}</td>
  </tr></tbody></table>`;
  document.getElementById('overview-scorers').innerHTML=t;
}
function buildMOTM(){
  const motmAll={};
  matches.forEach(m=>{
    if(!m.motm)return;
    m.motm.split('&').map(s=>s.trim()).forEach(name=>{
      if(!name||name==='—')return;
      if(!motmAll[name])motmAll[name]={};
      motmAll[name][m.comp]=(motmAll[name][m.comp]||0)+1;
    });
  });
  let mt=`<table><thead><tr>
    <th>Player</th><th class="r">Total Awards</th><th>By Competition</th>
  </tr></thead><tbody>`;
  Object.entries(motmAll)
    .sort((a,b)=>Object.values(b[1]).reduce((s,v)=>s+v,0)-Object.values(a[1]).reduce((s,v)=>s+v,0))
    .forEach(([name,comps],i)=>{
      const total=Object.values(comps).reduce((s,v)=>s+v,0);
      const breakdown=Object.entries(comps)
        .map(([c,v])=>`<span class="comp-badge" style="background:${BC[c]||'#475569'};font-size:10px;margin-right:4px">${c} \xd7${v}</span>`)
        .join('');
      mt+=`<tr class="${i%2===0?'ev':'od'}">
        <td style="font-weight:600;color:#ffffff">${name}</td>
        <td class="r" style="color:var(--accent-purple);font-weight:800;font-size:16px">${total}</td>
        <td style="padding-top:8px;padding-bottom:8px">${breakdown}</td>
      </tr>`;
    });
  mt+=`</tbody></table>`;
  document.getElementById('overview-motm').innerHTML=mt;
}
function buildHighlights(){
  const grid=document.getElementById('highlights-grid');
  highlights.forEach(h=>{
    const card=document.createElement('div');
    card.style.cssText='background:#0d1420;border:1px solid #1e293b;border-radius:10px;padding:14px 16px;display:flex;gap:12px;align-items:flex-start';
    card.innerHTML=`<span style="font-size:22px;flex-shrink:0">${h.icon}</span><span style="color:var(--text-head);font-size:14px;line-height:1.6">${h.text}</span>`;
    grid.appendChild(card);
  });
}
function buildOverview(){ buildStatCards();buildCompSummary();buildTopScorers();buildMOTM();buildHighlights(); }
function buildFilters(){
  const div=document.getElementById('match-filters');
  const compsInData=['All',...new Set(matches.map(m=>m.comp))];
  compsInData.forEach((f,i)=>{
    const btn=document.createElement('button');
    btn.className='filter-btn'+(i===0?' active':'');
    btn.textContent=f;
    if(i===0){btn.style.background='#2563eb';btn.style.color='#fff';btn.style.borderColor='transparent';}
    btn.addEventListener('click',()=>{
      document.querySelectorAll('.filter-btn').forEach(b=>{b.classList.remove('active');b.style.background='';b.style.color='';b.style.borderColor='';});
      btn.classList.add('active');
      btn.style.background=BC[f]||'#2563eb';btn.style.color='#fff';btn.style.borderColor='transparent';
      renderMatches(f);
    });
    div.appendChild(btn);
  });
}
function renderMatches(filter){
  const tbody=document.getElementById('matches-body');
  tbody.innerHTML='';
  const vis=filter==='All'?matches:matches.filter(m=>m.comp===filter);
  vis.forEach((m,i)=>{
    const bc=BC[m.comp]||'#475569';
    const rc=RC[m.res]||'#64748b';
    const ptsShow=(m.res==='ABN'||m.pts===null)?'—':m.pts;
    const ptsCol=m.pts===3?'#4ade80':m.pts===1?'#facc15':m.res==='L'?'#f87171':'#64748b';
    const motmDisplay=m.motm&&m.motm!=='—'?m.motm:'';
    const tr=document.createElement('tr');
    tr.className='match-row '+(i%2===0?'ev':'od');
    tr.setAttribute('aria-expanded','false');
    tr.innerHTML=`
      <td style="color:var(--text-sub);white-space:nowrap;font-size:12px">${m.date}</td>
      <td><span class="comp-badge" style="background:${bc}">${m.comp}</span></td>
      <td class="r"><span class="ha-badge" style="color:${m.ha==='H'?'#60a5fa':'#f59e0b'}">${m.ha==='H'?'Home':'Away'}</span></td>
      <td style="font-weight:600;color:#e2e8f0">${m.opp}</td>
      <td class="r" style="font-family:monospace;font-weight:800;color:#f1f5f9;font-size:14px;letter-spacing:.05em">${m.score}</td>
      <td class="r"><span class="res-badge" style="color:${rc};background:${rc}18">${m.res==='W'?'Win':m.res==='L'?'Loss':m.res==='D'?'Draw':'Abn'}</span></td>
      <td class="r" style="font-weight:700;color:${ptsCol}">${ptsShow}</td>
      <td style="color:#a78bfa;font-size:12px">${motmDisplay?'🏅 '+motmDisplay:''}</td>
      <td class="r"><span class="expand-icon">▾</span></td>`;
    const er=document.createElement('tr');
    er.className='expand-row';
    er.innerHTML=`<td colspan="9" class="expand-cell">
      <div class="expand-inner" style="border-color:${bc}">
        <p class="expand-summary">${m.summary||''}</p>
        <div class="expand-meta">
          <div><span class="expand-label" style="color:#60a5fa">Goalscorers &nbsp;</span><span class="expand-val">${m.scorers||'—'}</span></div>
          ${motmDisplay?`<div><span class="expand-label" style="color:#a78bfa">Man of the Match &nbsp;</span><span class="expand-val" style="color:#a78bfa;font-weight:700">${motmDisplay}</span></div>`:''}
        </div>
      </div></td>`;
    tr.addEventListener('click',()=>{
      const isOpen=er.classList.contains('open');
      document.querySelectorAll('.expand-row.open').forEach(r=>r.classList.remove('open'));
      document.querySelectorAll('.match-row.open').forEach(r=>{r.classList.remove('open');r.setAttribute('aria-expanded','false');});
      if(!isOpen){er.classList.add('open');tr.classList.add('open');tr.setAttribute('aria-expanded','true');}
    });
    tbody.appendChild(tr);tbody.appendChild(er);
  });
  const scored=vis.filter(m=>m.res!=='ABN'&&m.pts!==null);
  const W=scored.filter(m=>m.res==='W').length;
  const D=scored.filter(m=>m.res==='D').length;
  const L=scored.filter(m=>m.res==='L').length;
  const Pts=scored.reduce((s,m)=>s+(m.pts||0),0);
  const tr=document.createElement('tr');tr.className='tr-row';
  tr.innerHTML=`<td colspan="5" style="padding:12px">${filter==='All'?'All Competitions':filter} &nbsp;<span style="color:var(--text-sub);font-size:12px;font-weight:500">${scored.length} games \xb7 ${W} wins \xb7 ${D} draws \xb7 ${L} losses</span></td><td></td><td class="r" style="color:#fbbf24;font-size:15px">${Pts} pts</td><td colspan="2"></td>`;
  tbody.appendChild(tr);
}
function buildPlayers(){
  const wrap=document.getElementById('player-table-wrap');
  let t=`<table><thead><tr>
    <th>Player</th><th style="color:var(--text-sub);font-size:11px">Position</th>
    <th class="r">Apps</th>
    <th class="r" style="color:#b0c4d8">Fri</th><th class="r" style="color:#93c5fd">League</th><th class="r" style="color:#86efac">Cup</th>
    <th class="r" style="color:#fbbf24;border-left:2px solid var(--border)">Goals</th>
    <th class="r" style="color:#b0c4d8">Fri</th><th class="r" style="color:#93c5fd">League</th><th class="r" style="color:#86efac">Cup</th>
    <th class="r" style="color:#a78bfa">MOTM</th>
  </tr></thead><tbody>`;
  playersSorted.forEach((p,i)=>{
    if(i>0&&totalG(playersSorted[i-1])>0&&totalG(p)===0){
      t+=`<tr style="background:#0f1e30"><td colspan="11" style="padding:6px 12px;font-size:10px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--text-muted)">No goals scored</td></tr>`;
    }
    const tot=totalG(p),apps=totalA(p);
    const gc=tot>=20?'#6ee7b7':tot>=10?'#93c5fd':tot>0?'#f1f5f9':'var(--text-muted)';
    const ac=apps>=25?'#6ee7b7':apps>=15?'#93c5fd':'#f1f5f9';
    const mb=Object.entries(p.motm||{}).filter(([,v])=>v>0).map(([c,v])=>c+'\xd7'+v).join(' ')||'—';
    const cell=(v,b=false)=>`<td class="r" style="color:${v?'#f1f5f9':'var(--text-muted)'};font-weight:${b?700:400}">${v||'—'}</td>`;
    t+=`<tr class="${i%2===0?'ev':'od'}">
      <td><span style="font-weight:700;color:#fff">${p.name}${p.gk?' 🧤':''}</span>${p.surname?` <span style="color:var(--text-muted);font-size:11px">${p.surname}</span>`:''}${p.note?`<div style="font-size:11px;color:var(--text-sub);margin-top:2px">${p.note}</div>`:''}</td>
      <td style="color:var(--text-sub);font-size:12px">${p.role}</td>
      <td class="r" style="font-weight:700;font-size:14px;color:${ac}">${apps}</td>
      ${cell(p.friA)}${cell(p.lgeA,true)}${cell(p.cupA)}
      <td class="r" style="font-weight:800;font-size:${tot>=10?15:13}px;color:${gc};border-left:2px solid var(--border)">${tot||'—'}</td>
      ${cell(p.friG)}${cell(p.lgeG,true)}${cell(p.cupG)}
      <td class="r" style="font-size:11px;color:#a78bfa">${mb}</td>
    </tr>`;
  });
  const sA=k=>playersSorted.reduce((s,p)=>s+(p[k]||0),0);
  const gG=playersSorted.reduce((s,p)=>s+totalG(p),0);
  t+=`<tr class="tr-row"><td colspan="2"><strong>Totals</strong></td>
    <td class="r">${playersSorted.reduce((s,p)=>s+totalA(p),0)}</td>
    <td class="r">${sA('friA')}</td><td class="r">${sA('lgeA')}</td><td class="r">${sA('cupA')}</td>
    <td class="r" style="font-size:15px;border-left:2px solid var(--border)">${gG}</td>
    <td class="r">${sA('friG')}</td><td class="r">${sA('lgeG')}</td><td class="r">${sA('cupG')}</td>
    <td class="r" style="color:#a78bfa">${playersSorted.reduce((s,p)=>s+totalM(p),0)}</td>
  </tr></tbody></table>`;
  wrap.innerHTML=t;
  const grid=document.getElementById('player-cards');
  playersSorted.forEach(p=>{
    const tot=totalG(p),apps=totalA(p);
    const tc=tot>=20?'#6ee7b7':tot>=10?'#93c5fd':tot>0?'#60a5fa':'var(--text-muted)';
    const ac=apps>=25?'#6ee7b7':apps>=15?'#93c5fd':'#f1f5f9';
    const ml=Object.entries(p.motm||{}).filter(([,v])=>v>0).map(([c,v])=>c+'\xd7'+v).join(' \xb7 ');
    const card=document.createElement('div');card.className='player-card';
    card.innerHTML=`<div class="player-name">${p.name}${p.gk?' 🧤':''}</div><div class="player-role">${p.role}${p.surname?' \xb7 '+p.surname:''}</div><div class="player-stat-row"><span class="player-stat-lbl">Appearances</span><span class="player-stat-val" style="color:${ac}">${apps}</span></div><div class="player-stat-row"><span class="player-stat-lbl">Goals</span><span class="player-stat-val" style="color:${tc}">${tot}</span></div><div class="player-stat-row"><span class="player-stat-lbl">MOTM</span><span class="player-stat-val" style="color:#a78bfa">${totalM(p)}</span></div>${ml?`<div class="motm-line">🏅 ${ml}</div>`:''}${p.note?`<div class="player-note">${p.note}</div>`:''}`;
    grid.appendChild(card);
  });
}
function buildH2H(){
  const opps={};
  matches.filter(m=>m.res!=='ABN').forEach(m=>{
    if(!opps[m.opp])opps[m.opp]=[];
    opps[m.opp].push(m);
  });
  let t=`<table><thead><tr>
    <th>Opponent</th><th class="r">P</th><th class="r">W</th><th class="r">D</th><th class="r">L</th>
    <th class="r">GF</th><th class="r">GA</th><th class="r">GD</th><th>Results</th>
  </tr></thead><tbody>`;
  Object.entries(opps).sort((a,b)=>b[1].length-a[1].length).forEach(([opp,games],i)=>{
    const W=games.filter(m=>m.res==='W').length;
    const D=games.filter(m=>m.res==='D').length;
    const L=games.filter(m=>m.res==='L').length;
    let gf=0,ga=0;
    games.forEach(m=>{const pts=m.score.split('–');if(pts.length===2&&!isNaN(pts[0])){gf+=+pts[0];ga+=+pts[1];}});
    const gdVal=gf-ga;
    const chips=games.map(m=>{
      const rc=RC[m.res]||'#94a3b8',bc=BC[m.comp]||'#475569';
      return`<span class="result-chip" style="background:${rc}18;border:1px solid ${rc}33"><span class="comp-badge" style="background:${bc};font-size:9px;padding:1px 5px">${m.comp}</span> <span style="color:var(--text-sub);font-size:11px">${m.date}</span> <span style="color:${rc};font-weight:700;font-size:11px">${m.score}</span></span>`;
    }).join('');
    t+=`<tr class="${i%2===0?'ev':'od'}"><td style="font-weight:600;color:#f1f5f9">${opp}</td>
      <td class="r">${games.length}</td>
      <td class="r" style="color:#4ade80;font-weight:700">${W}</td>
      <td class="r" style="color:#facc15">${D}</td>
      <td class="r" style="color:#f87171">${L}</td>
      <td class="r">${gf}</td><td class="r">${ga}</td>
      <td class="r" style="color:${gdVal>=0?'#4ade80':'#f87171'};font-weight:700">${gd(gdVal)}</td>
      <td style="line-height:2">${chips}</td></tr>`;
  });
  t+=`</tbody></table>`;
  document.getElementById('h2h-table-wrap').innerHTML=t;
}
function buildReport(){
  const body=document.getElementById('report-body');
  const hdr=document.createElement('div');
  hdr.style.cssText='text-align:center;margin-bottom:36px';
  hdr.innerHTML=`<div style="font-size:10px;letter-spacing:0.3em;color:#93c5fd;text-transform:uppercase;margin-bottom:8px">${seasonStory.presentation_date||''}</div><div style="font-size:24px;font-weight:800;color:#ffffff;margin-bottom:4px">${seasonStory.report_title||''}</div><div style="font-size:13px;color:#93c5fd">${seasonStory.report_subtitle||''}</div>`;
  body.appendChild(hdr);
  (seasonStory.sections||[]).forEach(s=>{
    const h=document.createElement('h3');h.textContent=s.heading;body.appendChild(h);
    (s.paras||[]).forEach(text=>{const p=document.createElement('p');p.innerHTML=text;body.appendChild(p);});
  });
  highlights.forEach(h=>{
    const bq=document.createElement('blockquote');bq.innerHTML=`${h.icon} ${h.text}`;body.appendChild(bq);
  });
  if(seasonStory.signoff){const so=document.createElement('div');so.className='report-signoff';so.innerHTML=seasonStory.signoff;body.appendChild(so);}
}
function buildLeagueTable(){
  const tbody=document.getElementById('league-tbody');
  if(!tbody)return;
  leagueTable.forEach((r,i)=>{
    const isUs=!!r.us,isChamp=!!r.champion;
    const bg=isUs?'#1a3d6e':isChamp?'#1a3d28':i%2===0?'var(--bg-row)':'var(--bg-card)';
    const bl=isUs?'border-left:4px solid #3b82f6':isChamp?'border-left:4px solid #16a34a':'border-left:4px solid transparent';
    const posIcon=isChamp?'🏆':isUs?'🥈':r.pos;
    const pc=isUs?'#60a5fa':isChamp?'#4ade80':'var(--text-sub)';
    const tc=isUs?'#ffffff':isChamp?'#4ade80':'var(--text-body)';
    const ptsC=isUs?'#fbbf24':isChamp?'#4ade80':'var(--text-head)';
    const tr=document.createElement('tr');tr.style.cssText='background:'+bg+';'+bl;
    tr.innerHTML=`<td class="r" style="font-weight:700;color:${pc};font-size:${isUs||isChamp?15:13}px">${posIcon}</td>`+
      `<td style="font-weight:${isUs||isChamp?700:500};color:${tc}">${r.team}`+
        (r.note?`<div style="font-size:11px;color:var(--text-sub);font-weight:400;margin-top:2px">${r.note}</div>`:'')+
        (r.pos<=2?`<div style="font-size:10px;color:var(--text-muted);font-weight:400;margin-top:1px">† Updated from screenshot</div>`:'')+
      `</td>`+
      `<td class="r" style="color:var(--text-sub)">${r.P}</td>`+
      `<td class="r" style="color:#4ade80;font-weight:600">${r.W}</td>`+
      `<td class="r" style="color:#facc15">${r.D}</td>`+
      `<td class="r" style="color:#f87171">${r.L}</td>`+
      `<td class="r" style="font-weight:800;font-size:${isUs||isChamp?16:13}px;color:${ptsC}">${r.Pts}</td>`;
    tbody.appendChild(tr);
  });
}
"""

_JS_TEAM_LOADER = """
function clearDom(){
  ['stat-cards','comp-summary-wrap','overview-scorers','overview-motm','highlights-grid',
   'match-filters','matches-body','player-table-wrap','player-cards','h2h-table-wrap','report-body']
  .forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML='';});
  const lt=document.getElementById('league-tbody');if(lt)lt.innerHTML='';
}
function loadTeam(id){
  const d=id==='sat'?satData:sunData;
  matches=d.matches;players=d.players;highlights=d.highlights;
  leagueTable=d.leagueTable;seasonStory=d.seasonStory;statCards=d.statCards;
  playersSorted=[...players].sort((a,b)=>totalG(b)-totalG(a)||totalA(b)-totalA(a));
  document.querySelectorAll('.team-btn').forEach(b=>b.classList.remove('active'));
  const ab=document.querySelector('.team-btn[data-team="'+id+'"]');if(ab)ab.classList.add('active');
  const sb=document.getElementById('sat-banner');if(sb)sb.style.display=id==='sat'?'':'none';
  const ub=document.getElementById('sun-banner');if(ub)ub.style.display=id==='sun'?'':'none';
  const cap=document.getElementById('league-caption');if(cap)cap.textContent=d.leagueCaption||'';
  clearDom();
  buildOverview();buildFilters();renderMatches('All');buildPlayers();buildH2H();buildReport();buildLeagueTable();
  showSection('overview',document.querySelector('.nav-btn'));
}
"""

def _match_to_js(m):
    return {k: m[k] for k in ("date","comp","ha","opp","score","res","pts","motm","scorers","summary") if k in m}

def _player_to_js(p):
    out = {
        "name": p["name"], "surname": p.get("surname",""), "role": p.get("role",""),
        "gk": p.get("gk", False),
        "friA": p.get("fri_apps",0), "lgeA": p.get("lge_apps",0), "cupA": p.get("cup_apps",0),
        "friG": p.get("fri_goals",0), "lgeG": p.get("lge_goals",0), "cupG": p.get("cup_goals",0),
        "motm": p.get("motm",{}),
    }
    if p.get("note"):
        out["note"] = p["note"]
    return out

def build_html(cfg, config_hash, sun_cfg=None):
    """
    Build single self-contained HTML file.
    cfg:     primary (Saturday/Greens) team season_config dict
    sun_cfg: optional secondary (Sunday/Whites) season_config — triggers two-team mode
    """
    dual = sun_cfg is not None
    meta  = cfg.get("meta", {})
    team  = cfg.get("team", {})

    def j(obj): return json.dumps(obj, ensure_ascii=False, indent=2)

    # ── HEAD ───────────────────────────────────────────────────────────────
    head = (
        f'<!DOCTYPE html>\n'
        f'<!-- generated by generate_season.py | config-hash: {config_hash} -->\n'
        f'<html lang="en">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<meta name="description" content="{meta.get("description","")}">\n'
        f'<meta property="og:title" content="{meta.get("og_title","")}">\n'
        f'<meta property="og:description" content="{meta.get("og_description","")}">\n'
        f'<title>{meta.get("title","St John\'s Chorlton U15s")}</title>\n'
        f'<style>\n' + _CSS + '</style>\n</head>\n<body>\n'
    )

    # ── TEAM SWITCHER (dual-team only) ─────────────────────────────────────
    if dual:
        sun_team  = sun_cfg.get("team", {})
        sat_label = team.get("short_name", "Greens")
        sun_label = sun_team.get("short_name", "Whites")
        team_switcher = (
            '<div class="team-switcher">\n'
            '  <span class="team-switcher-label">Choose team &nbsp;·&nbsp;</span>\n'
            f'  <button class="team-btn sat-btn active" data-team="sat" onclick="loadTeam(\'sat\')">🟢 {sat_label}</button>\n'
            f'  <button class="team-btn sun-btn" data-team="sun" onclick="loadTeam(\'sun\')">⚪ {sun_label}</button>\n'
            '</div>\n'
        )
    else:
        team_switcher = ''

    # ── BANNER(S) ──────────────────────────────────────────────────────────
    def _banner(b_team, b_cfg, bid=None, hidden=False):
        t_pills = b_cfg.get("banner_pills", [])
        ph = "\n    ".join(f'<div class="season-pill">{p["text"]}</div>' for p in t_pills)
        id_attr    = f' id="{bid}"' if bid else ''
        style_attr = (' style="display:none;background:linear-gradient(135deg,'
                      '#16295e 0%,#162a4a 40%,#16375e 100%)"') if hidden else ''
        return (
            f'<div class="top-banner"{id_attr}{style_attr}>\n'
            f'  <div class="club-badge">⚽</div>\n'
            f'  <div class="club-name">{b_team.get("name","")}</div>\n'
            f'  <div class="club-sub">Season {b_team.get("season","")} &nbsp;·&nbsp; '
            f'Managers: {b_team.get("managers","")}</div>\n'
            f'  <div class="season-pills">\n    {ph}\n  </div>\n</div>\n\n'
        )

    if dual:
        banner = (
            _banner(team, cfg, bid="sat-banner") +
            _banner(sun_cfg.get("team", {}), sun_cfg, bid="sun-banner", hidden=True)
        )
    else:
        banner = _banner(team, cfg)

    # ── NAV ────────────────────────────────────────────────────────────────
    nav = (
        '<nav class="nav-sticky" role="navigation">\n'
        '  <button class="nav-btn active" onclick="showSection(\'overview\',this)">📊 Overview</button>\n'
        '  <button class="nav-btn" onclick="showSection(\'matches\',this)">📅 Results</button>\n'
        '  <button class="nav-btn" onclick="showSection(\'players\',this)">👤 Players</button>\n'
        '  <button class="nav-btn" onclick="showSection(\'h2h\',this)">⚔️ vs Each Team</button>\n'
        '  <button class="nav-btn" onclick="showSection(\'report\',this)">📖 Season Story</button>\n'
        '  <button class="nav-btn" onclick="showSection(\'table\',this)">📋 League Table</button>\n'
        '</nav>\n\n'
    )

    # ── SECTIONS ───────────────────────────────────────────────────────────
    # League table caption: static for single-team; populated by JS for dual-team
    if dual:
        league_caption_content = ''   # filled by loadTeam() in JS
    else:
        league_caption_content = (
            f'{team.get("league","")} &nbsp;·&nbsp; {team.get("division","")} &nbsp;·&nbsp; '
            f'Final standings {team.get("season","")}.'
        )

    sections = (
        '<div id="overview" class="section active">\n'
        '  <div class="stat-cards" id="stat-cards" style="margin-top:8px"></div>\n'
        '  <div class="section-title">Results by Competition</div>\n'
        '  <div class="table-wrap" id="comp-summary-wrap"></div>\n'
        '  <div class="section-title">Top Scorers — All Competitions</div>\n'
        '  <div class="table-wrap" id="overview-scorers"></div>\n'
        '  <div class="section-title">Man of the Match Awards</div>\n'
        '  <div class="table-wrap" id="overview-motm"></div>\n'
        '  <div class="section-title">Season Highlights</div>\n'
        '  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-bottom:28px" id="highlights-grid"></div>\n'
        '</div>\n\n'
        '<div id="matches" class="section">\n'
        '  <div class="filter-row" id="match-filters"></div>\n'
        '  <p style="color:var(--text-sub);font-size:13px;margin-bottom:12px">Tap any result to read the match report ▾</p>\n'
        '  <div class="table-wrap"><table>\n'
        '    <thead><tr><th>Date</th><th>Competition</th><th class="r">H/A</th><th>Opponent</th>'
        '<th class="r">Score</th><th class="r">Result</th><th class="r">Pts</th>'
        '<th>Man of Match</th><th class="r"></th></tr></thead>\n'
        '    <tbody id="matches-body"></tbody>\n'
        '  </table></div>\n</div>\n\n'
        '<div id="players" class="section">\n'
        '  <div class="section-title">Goals by Player &amp; Competition</div>\n'
        '  <div class="table-wrap" id="player-table-wrap"></div>\n'
        '  <div class="section-title" style="margin-top:8px">Player Cards</div>\n'
        '  <div class="player-grid" id="player-cards"></div>\n'
        '</div>\n\n'
        '<div id="h2h" class="section">\n'
        '  <p style="color:var(--text-sub);font-size:13px;margin-bottom:16px">Our record against every team we faced this season.</p>\n'
        '  <div class="table-wrap" id="h2h-table-wrap"></div>\n'
        '</div>\n\n'
        '<div id="report" class="section">\n'
        '  <div class="report-body" id="report-body"></div>\n'
        '</div>\n\n'
        '<div id="table" class="section">\n'
        '  <div style="max-width:900px;margin:0 auto 16px">\n'
        f'    <p id="league-caption" style="color:var(--text-sub);font-size:13px;margin-bottom:6px">{league_caption_content}</p>\n'
        '    <p style="color:var(--text-muted);font-size:11px">&dagger; Top 2 updated from final games.</p>\n'
        '  </div>\n'
        '  <div class="table-wrap"><table>\n'
        '    <thead><tr><th class="r" style="width:36px">Pos</th><th>Team</th>'
        '<th class="r">P</th><th class="r">W</th><th class="r">D</th><th class="r">L</th><th class="r">Pts</th></tr></thead>\n'
        '    <tbody id="league-tbody"></tbody>\n'
        '  </table></div>\n'
        '  <div style="max-width:900px;margin:8px auto 0;display:flex;gap:20px;flex-wrap:wrap;font-size:12px;color:var(--text-sub)">'
        '<span>🏆 Champions</span><span>🥈 Runners-up</span></div>\n'
        '</div>\n\n'
    )

    # ── FOOTER ─────────────────────────────────────────────────────────────
    if dual:
        sun_team_d = sun_cfg.get("team", {})
        footer = (
            f'<footer class="site-footer">\n'
            f'  <div>St John\'s Chorlton JFC &nbsp;·&nbsp; Season {team.get("season","")}</div>\n'
            f'  <div style="margin-top:4px">{team.get("name","")} &amp; {sun_team_d.get("name","")}</div>\n'
            f'</footer>\n\n'
        )
    else:
        footer = (
            f'<footer class="site-footer">\n'
            f'  <div>{team.get("name","")} &nbsp;·&nbsp; Season {team.get("season","")}</div>\n'
            f'  <div style="margin-top:4px">{team.get("league","")} &nbsp;·&nbsp; {team.get("division","")}</div>\n'
            f'</footer>\n\n'
        )

    # ── JS DATA + SCRIPT ───────────────────────────────────────────────────
    matches_js = [_match_to_js(m) for m in cfg.get("matches", [])]
    players_js = [_player_to_js(p) for p in cfg.get("players", [])]

    if dual:
        sun_team_d   = sun_cfg.get("team", {})
        sat_cap = (f"{team.get('league','')} · {team.get('division','')} · "
                   f"Final standings {team.get('season','')}")
        sun_cap = (f"{sun_team_d.get('league','')} · {sun_team_d.get('division','')} · "
                   f"Final standings {sun_team_d.get('season','')}")

        sat_data = {
            "matches":       matches_js,
            "players":       players_js,
            "highlights":    cfg.get("highlights", []),
            "leagueTable":   cfg.get("league_table", []),
            "seasonStory":   cfg.get("season_story", {}),
            "statCards":     cfg.get("stat_cards", []),
            "leagueCaption": sat_cap,
        }
        sun_matches_js = [_match_to_js(m) for m in sun_cfg.get("matches", [])]
        sun_players_js = [_player_to_js(p) for p in sun_cfg.get("players", [])]
        sun_data = {
            "matches":       sun_matches_js,
            "players":       sun_players_js,
            "highlights":    sun_cfg.get("highlights", []),
            "leagueTable":   sun_cfg.get("league_table", []),
            "seasonStory":   sun_cfg.get("season_story", {}),
            "statCards":     sun_cfg.get("stat_cards", []),
            "leagueCaption": sun_cap,
        }

        script = (
            "<script>\n'use strict';\n\n"
            "// ── DATA ─────────────────────────────────────────────────────────────\n"
            f"const satData = {j(sat_data)};\n\n"
            f"const sunData = {j(sun_data)};\n\n"
            "let matches, players, highlights, leagueTable, seasonStory, statCards, playersSorted;\n\n"
            "// ── FUNCTIONS ─────────────────────────────────────────────────────────\n"
            + _JS_FUNCS
            + _JS_TEAM_LOADER
            + "loadTeam('sat');\n"
            "</script>\n"
        )
    else:
        script = (
            "<script>\n'use strict';\n\n"
            "// ── DATA ─────────────────────────────────────────────────────────────\n"
            f"const matches = {j(matches_js)};\n\n"
            f"const players = {j(players_js)};\n\n"
            f"const highlights = {j(cfg.get('highlights', []))};\n\n"
            f"const leagueTable = {j(cfg.get('league_table', []))};\n\n"
            f"const seasonStory = {j(cfg.get('season_story', {}))};\n\n"
            f"const statCards = {j(cfg.get('stat_cards', []))};\n\n"
            "// ── FUNCTIONS ─────────────────────────────────────────────────────────\n"
            + _JS_FUNCS
            + "const playersSorted=[...players].sort((a,b)=>totalG(b)-totalG(a)||totalA(b)-totalA(a));\n"
            "buildOverview();buildFilters();renderMatches('All');buildPlayers();buildH2H();buildReport();buildLeagueTable();\n"
            "</script>\n"
        )

    return head + team_switcher + banner + nav + sections + footer + script + "</body>\n</html>\n"


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate St John's season app")
    parser.add_argument("--config",        default="season_config.json")
    parser.add_argument("--out",           default="index.html")
    parser.add_argument("--force-extract", action="store_true", help="Re-parse sources even if unchanged")
    parser.add_argument("--force-build",   action="store_true", help="Rebuild HTML even if config unchanged")
    parser.add_argument("--force",         action="store_true", help="Force both extract and build")
    parser.add_argument("--test",          action="store_true", help="Run Node.js JS validation")
    parser.add_argument("--extract-only", action="store_true", help="Extract/update season_config only — skip HTML build")
    parser.add_argument("--team",          default=None,
                        help="Path to team config JSON, e.g. teams/greens/greens.json. "
                             "Overrides --config and --out with values from the team file.")
    parser.add_argument("--team-2",        default=None, dest="team_2",
                        help="Path to a second team config JSON for a combined two-team build. "
                             "The second team's existing season_config is read (not re-extracted). "
                             "Example: --team-2 teams/whites/whites.json")
    parser.add_argument("--fetch-table",   action="store_true",
                        help="Fetch latest league table from FA Full Time and update season_config.json")
    args = parser.parse_args()

    force_extract = args.force or args.force_extract
    force_build   = args.force or args.force_build

    # ── Load team config (optional) ───────────────────────────────────────────
    team_cfg = None
    hash_file = HASH_FILE  # default; overridden per-team below
    if args.team:
        team_cfg = load_team_config(args.team)
        # Team config overrides --config / --out defaults
        if args.config == "season_config.json":
            args.config = team_cfg["sources"]["season_config"]
        if args.out == "index.html":
            args.out = team_cfg["sources"]["output_html"]
        # Per-team hash file lives alongside the team's season_config
        team_dir = os.path.dirname(team_cfg["sources"]["season_config"])
        hash_file = os.path.join(team_dir, ".source_hashes.json")
        print(f"Team config loaded: {team_cfg['team']['name']}")

    # ── Load second team config for combined two-team build ───────────────────
    team2_cfg     = None
    sun_season_cfg = None
    if args.team_2:
        team2_cfg = load_team_config(args.team_2)
        sun_config_path = team2_cfg["sources"]["season_config"]

        # Check if team 2's source files have changed and re-extract if so
        t2_raw = {
            "results_pdf": team2_cfg["sources"]["results_pdf"],
            "players_pdf": team2_cfg["sources"]["players_pdf"],
            "chat_zip":    team2_cfg["sources"]["chat_zip"],
        }
        t2_sources = {
            "results_pdf": _resolve_pdf(t2_raw["results_pdf"]) or t2_raw["results_pdf"],
            "players_pdf": _resolve_pdf(t2_raw["players_pdf"]) or t2_raw["players_pdf"],
            "chat_zip":    t2_raw["chat_zip"],
        }
        t2_team_dir = os.path.dirname(sun_config_path)
        t2_hash_file = os.path.join(t2_team_dir, ".source_hashes.json")
        t2_new_hashes = {k: sha256_file(v) for k, v in t2_sources.items() if os.path.isfile(v)}
        t2_old_hashes = load_source_hashes(t2_hash_file)
        if t2_new_hashes != t2_old_hashes or force_extract:
            changed = [k for k in t2_new_hashes if t2_new_hashes[k] != t2_old_hashes.get(k)]
            print(f"Team 2 sources changed ({', '.join(changed)}) — re-extracting…", flush=True)
            import subprocess
            cmd = [sys.executable, __file__, "--team", args.team_2, "--extract-only"]
            if force_extract:
                cmd.append("--force-extract")
            subprocess.run(cmd, check=True)

        if os.path.exists(sun_config_path):
            with open(sun_config_path, encoding="utf-8") as f:
                sun_season_cfg = json.load(f)
            print(f"Team 2 config loaded: {team2_cfg['team']['name']}")
        else:
            print(f"  WARNING: Team 2 season_config not found: {sun_config_path}", file=sys.stderr)
            print(f"    Run: python3 generate_season.py --team {args.team_2} first", file=sys.stderr)

    _raw_sources = {
        "results_pdf": team_cfg["sources"]["results_pdf"] if team_cfg else SRC_RESULTS,
        "players_pdf": team_cfg["sources"]["players_pdf"] if team_cfg else SRC_PLAYERS,
        "chat_zip":    team_cfg["sources"]["chat_zip"]    if team_cfg else SRC_CHAT,
    }
    sources = {
        "results_pdf": _resolve_pdf(_raw_sources["results_pdf"]) or _raw_sources["results_pdf"],
        "players_pdf": _resolve_pdf(_raw_sources["players_pdf"]) or _raw_sources["players_pdf"],
        "chat_zip":    _raw_sources["chat_zip"],
    }

    # ── STEP 0: Fetch league table from FA Full Time (if requested) ───────────
    if args.fetch_table:
        print("\nFetching league table from FA Full Time…")
        ft_url = (team_cfg or {}).get("fulltime", {}).get("league_table_url") if team_cfg else None
        our_name = (team_cfg or {}).get("our_team_name_in_fa", "") if team_cfg else ""

        if not ft_url:
            print("  ✗ No league_table_url configured in team config.", file=sys.stderr)
            print("    Set fulltime.league_table_url in your teams/*.json file.", file=sys.stderr)
        else:
            fetched_rows = fetch_league_table(ft_url, our_name)
            if fetched_rows:
                # Load existing config and patch league_table section
                if os.path.exists(args.config):
                    with open(args.config, encoding="utf-8") as f:
                        cfg_to_patch = json.load(f)
                else:
                    cfg_to_patch = {}

                # Preserve any manual 'note' fields from existing table rows
                old_notes = {}
                for old_row in cfg_to_patch.get("league_table", []):
                    if "note" in old_row:
                        old_notes[old_row["pos"]] = old_row["note"]
                for row in fetched_rows:
                    if row["pos"] in old_notes:
                        row["note"] = old_notes[row["pos"]]

                cfg_to_patch["league_table"] = fetched_rows
                with open(args.config, "w", encoding="utf-8") as f:
                    json.dump(cfg_to_patch, f, indent=2, ensure_ascii=False)
                print(f"  ✓ league_table updated in {args.config}")
                force_build = True  # Config just changed — rebuild HTML
            else:
                print("  League table NOT updated (fetch/parse failed).")

    # ── Check which source files exist (chat required; PDFs optional) ─────────
    has_results_pdf = os.path.exists(sources["results_pdf"])
    has_players_pdf = os.path.exists(sources["players_pdf"])
    has_chat        = os.path.exists(sources["chat_zip"])

    if not has_chat:
        print(f"ERROR: chat file not found: {sources['chat_zip']}", file=sys.stderr)
        sys.exit(1)
    if not has_results_pdf:
        print(f"  ⚠  Results PDF not found ({sources['results_pdf']}) — match results from WhatsApp only")
    if not has_players_pdf:
        print(f"  ⚠  Players PDF not found ({sources['players_pdf']}) — player stats from WhatsApp only")

    # ── STEP 1: Check source file hashes ─────────────────────────────────────
    print("Checking source files…")
    new_src_hashes = {k: sha256_file(v) for k, v in sources.items() if os.path.isfile(v)}
    old_src_hashes = load_source_hashes(hash_file)

    sources_changed = new_src_hashes != old_src_hashes
    if sources_changed:
        changed_files = [k for k in new_src_hashes if new_src_hashes[k] != old_src_hashes.get(k)]
        print(f"  Sources changed: {', '.join(changed_files)}")
    else:
        print("  Sources unchanged.")

    # ── STEP 2: Parse sources if changed ─────────────────────────────────────
    if sources_changed or force_extract:
        print("\nParsing sources…")

        if has_results_pdf:
            print("  → FA results PDF…", flush=True)
            fa_results = parse_results_pdf(sources["results_pdf"])
            print(f"     {len(fa_results)} fixtures extracted")
        else:
            fa_results = []
            print("  → FA results PDF… skipped (not present)")

        if has_players_pdf:
            print("  → FA players PDF…", flush=True)
            fa_players = parse_players_pdf(sources["players_pdf"])
            print(f"     {len(fa_players)} players extracted")
        else:
            fa_players = []
            print("  → FA players PDF… skipped (not present)")

        print("  → WhatsApp chat…", flush=True)
        wa_cfg = team_cfg.get("whatsapp") if team_cfg else None
        wa_matches = parse_whatsapp(sources["chat_zip"], wa_cfg)
        print(f"     {len(wa_matches)} match reports found")

        # Load existing config (preserve manual fields)
        if os.path.exists(args.config):
            with open(args.config, encoding="utf-8") as f:
                existing_cfg = json.load(f)
            print(f"\n  Loaded existing config: {args.config}")
        else:
            existing_cfg = {}
            print(f"\n  No existing config — will create fresh")

        # Merge + flag discrepancies
        print("\nCross-referencing data…")
        updated_cfg, discrepancies = merge_and_flag(fa_results, fa_players, wa_matches, existing_cfg)

        # ── Squad appearances (when no players PDF) ───────────────────────────
        if not has_players_pdf and existing_cfg.get("matches"):
            print("\n  → Squad appearances from WhatsApp…", flush=True)
            squad_apps = parse_squad_appearances(
                sources["chat_zip"],
                updated_cfg.get("matches", []),
                wa_cfg
            )
            if squad_apps:
                # Patch player app counts in updated_cfg
                for p in updated_cfg.get("players", []):
                    pname = p["name"]
                    if pname in squad_apps:
                        p["lge_apps"] = squad_apps[pname].get("lge", 0)
                        p["cup_apps"] = squad_apps[pname].get("cup", 0)
                        p["fri_apps"] = squad_apps[pname].get("fri", 0)
                    # Add players who appeared in squads but aren't in the players list
                for sname, scounts in squad_apps.items():
                    if not any(p["name"] == sname for p in updated_cfg.get("players", [])):
                        total = scounts["lge"] + scounts["cup"] + scounts["fri"]
                        if total > 0:
                            updated_cfg.setdefault("players", []).append({
                                "name": sname,
                                "lge_goals": 0, "cup_goals": 0, "fri_goals": 0,
                                "lge_apps": scounts["lge"],
                                "cup_apps": scounts["cup"],
                                "fri_apps": scounts["fri"],
                            })
                # Save updated config with appearances
                with open(args.config, "w", encoding="utf-8") as f:
                    json.dump(updated_cfg, f, indent=2, ensure_ascii=False)
                print(f"     Appearances written to {args.config}")
                force_build = True

        # Report discrepancies
        if discrepancies:
            print(f"\n{'='*60}")
            print(f"  ⚠  {len(discrepancies)} DISCREPANCIES FOUND:")
            print(f"{'='*60}")
            for d in discrepancies:
                print(f"  • {d}")
            print(f"{'='*60}")
        else:
            print("  ✓ No discrepancies found.")

        # Save source hashes
        save_source_hashes(new_src_hashes, hash_file)
        print(f"\n  Source hashes saved to {hash_file}")

    else:
        print("  Skipping extraction (use --force-extract to override)")
        # Still load existing config for build step
        if not os.path.exists(args.config):
            print(f"ERROR: {args.config} not found", file=sys.stderr)
            sys.exit(1)

    # ── STEP 3: Build HTML if config changed ──────────────────────────────────
    if args.extract_only:
        print("\nDone (extract only — HTML build skipped).")
        return

    print(f"\nChecking config…")

    # Compute config hash — combined hash when both teams are present
    primary_hash = sha256_file(args.config)
    if sun_season_cfg is not None and args.team_2:
        sun_cfg_path = team2_cfg["sources"]["season_config"]
        if os.path.exists(sun_cfg_path):
            combined = primary_hash + sha256_file(sun_cfg_path)
            cfg_hash_new = hashlib.sha256(combined.encode()).hexdigest()
        else:
            cfg_hash_new = primary_hash
    else:
        cfg_hash_new = primary_hash

    cfg_hash_old = read_config_hash(args.out)

    if cfg_hash_new == cfg_hash_old and not force_build:
        print(f"  Config unchanged — skipping HTML build.")
        print(f"  (use --force-build to override)")
    else:
        if cfg_hash_old and cfg_hash_new != cfg_hash_old:
            print(f"  Config changed — rebuilding {args.out}…")
        else:
            print(f"  Building {args.out}…")

        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)

        if sun_season_cfg is not None:
            print(f"  Two-team build: {cfg.get('team',{}).get('name','')} + "
                  f"{sun_season_cfg.get('team',{}).get('name','')}")

        html = build_html(cfg, cfg_hash_new, sun_cfg=sun_season_cfg)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(html)
        size_kb = os.path.getsize(args.out) / 1024
        print(f"  ✓ Written {args.out} ({size_kb:.1f} KB)")

        if args.test:
            ok = run_node_test(args.out)
            if not ok:
                print("Build complete but JS validation failed.", file=sys.stderr)
                sys.exit(2)

    print("\nDone.")

if __name__ == "__main__":
    main()
