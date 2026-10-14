#!/usr/bin/env python3
"""Daily NBA feed: games + DraftKings game lines + DraftKings player props + injuries.

Runs anywhere with open internet (the GitHub Actions runner). Python stdlib only.
Writes exact raw JSON (no summarizer in the path) plus flat CSVs:

  feed/out/YYYY-MM-DD/games.csv        one row per game: ids, teams, tip, spread, total, moneylines
  feed/out/YYYY-MM-DD/props.csv        one row per prop price (ESPN athlete id = hoopR athlete_id)
  feed/out/YYYY-MM-DD/injuries.csv     one row per injured player
  feed/out/YYYY-MM-DD/manifest.json    row counts, page checks, pass/fail per game
  feed/out/YYYY-MM-DD/raw/...          raw JSON pages as received

Integrity rules (spec Part 4):
  * Every propBets page must contain min(pageSize, count - (page-1)*pageSize) items.
    A short page is re-pulled (3 tries); if it still fails, that game is marked FAIL
    in the manifest and its props are NOT written (no partial boards).
  * Total rows written per game must equal the API's `count`.

Usage:  python fetch_daily.py [YYYY-MM-DD]     (default: today, US/Eastern)
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
DK = 100            # ESPN provider id for DraftKings
PAGE = 100          # props per page
UA = {"User-Agent": "Mozilla/5.0 (nba-engine feed)"}


def get_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:          # network or JSON error: back off and retry
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {url}: {last}")


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def athlete_id(ref):
    m = re.search(r"/athletes/(\d+)", ref or "")
    return int(m.group(1)) if m else None


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------- games + game lines ----------
def parse_games(sb):
    rows = []
    for ev in sb.get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        h, a = teams["home"], teams["away"]
        odds = next((o for o in comp.get("odds", [])
                     if str(o.get("provider", {}).get("id")) == str(DK)), None) \
            or (comp.get("odds") or [None])[0]
        row = dict(event_id=ev["id"], tip_utc=ev["date"],
                   status=ev.get("status", {}).get("type", {}).get("name"),
                   home=h["team"]["abbreviation"], away=a["team"]["abbreviation"],
                   home_team_id=h["team"]["id"], away_team_id=a["team"]["id"],
                   provider=None, details=None, spread_home=None, total=None,
                   ml_home=None, ml_away=None)
        if odds:
            row["provider"] = odds.get("provider", {}).get("name")
            row["details"] = odds.get("details")
            row["total"] = num(odds.get("overUnder"))
            sp = num(odds.get("spread"))
            # ESPN `spread` is quoted from the home team's side (book sign, home fav < 0).
            # Cross-check against `details` ("DET -1.5"): if the named team is the away
            # team, flip. Spec Layer 1 uses book sign: implied_home = total/2 - spread_home/2.
            d = (odds.get("details") or "").split()
            if sp is not None and len(d) == 2 and num(d[1]) is not None:
                fav, val = d[0], num(d[1])
                if fav == row["home"]:
                    sp = val
                elif fav == row["away"]:
                    sp = -val
            row["spread_home"] = sp
            for side, key in (("home", "homeTeamOdds"), ("away", "awayTeamOdds")):
                ml = (odds.get(key) or {}).get("moneyLine")
                row[f"ml_{side}"] = ml
        if row["total"] is not None and row["spread_home"] is not None:
            row["implied_home"] = row["total"] / 2 - row["spread_home"] / 2
            row["implied_away"] = row["total"] / 2 + row["spread_home"] / 2
        rows.append(row)
    return rows


# ---------- player props ----------
def fetch_props(event_id, rawdir):
    """Return (rows, check) with full verification; rows=None if the board failed."""
    base = f"{CORE}/events/{event_id}/competitions/{event_id}/odds/{DK}/propBets"
    first = get_json(f"{base}?limit={PAGE}&page=1")
    count, pages = first.get("count", 0), first.get("pageCount", 0)
    items, checks = [], []
    for p in range(1, pages + 1):
        expect = min(PAGE, count - (p - 1) * PAGE)
        page = first if p == 1 else None
        for attempt in range(3):
            if page is None:
                page = get_json(f"{base}?limit={PAGE}&page={p}")
            got = len(page.get("items", []))
            if got == expect and page.get("count") == count:
                break
            page = None
            time.sleep(2)
        ok = page is not None
        checks.append(dict(page=p, expected=expect, received=got, ok=ok))
        if not ok:
            return None, dict(event_id=event_id, count=count, pages=pages, ok=False, pages_detail=checks)
        save(os.path.join(rawdir, f"props_{event_id}_p{p}.json"), page)
        items.extend(page["items"])
    ok = len(items) == count
    rows = []
    for i, it in enumerate(items):
        o = it.get("odds", {})
        rows.append(dict(
            event_id=event_id, idx=i,
            athlete_id=athlete_id(it.get("athlete", {}).get("$ref")),
            type_id=it.get("type", {}).get("id"),
            type_name=it.get("type", {}).get("name"),
            line=(it.get("current", {}).get("target") or {}).get("value"),
            line_open=(it.get("open", {}).get("target") or {}).get("value"),
            american=(o.get("american") or {}).get("value"),
            american_open=(o.get("american") or {}).get("open"),
            last_updated=it.get("lastUpdated")))
    side = label_sides(rows)
    return (rows if ok else None), dict(event_id=event_id, count=count, pages=pages,
                                        received=len(items), ok=ok, pages_detail=checks, **side)


SIDE_ORDER = os.environ.get("PROP_SIDE_ORDER", "AUTO")
# Milestone ("X+") markets are one-sided overs. "Over X.5" is the same bet as "X+1 or more",
# so a paired line whose first price matches the X+1 ladder price is OVER-first.
# Verified 14/14 on BOS@DET 2026-10-14 (every gap <= 0.5 prob pts). Re-checked per board.
LADDER_OF = {"1": "218", "2": "219", "3": "220"}   # PTS/AST/REB line type -> milestone type


def implied(a):
    a = num(a)
    if a is None:
        return None
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def verify_side_order(rows):
    """Return ('OVER_FIRST'|'UNDER_FIRST'|'UNVERIFIED', n_checked, n_over_first)."""
    ladder = {(r["athlete_id"], r["type_id"], num(r["line"])): implied(r["american"])
              for r in rows if r["type_id"] in LADDER_OF.values()}
    pairs = {}
    for r in rows:
        if r["type_id"] in LADDER_OF and r["line"] is not None:
            pairs.setdefault((r["athlete_id"], r["type_id"], num(r["line"])), []).append(implied(r["american"]))
    n = first = 0
    for (aid, t, ln), pr in pairs.items():
        m = ladder.get((aid, LADDER_OF[t], ln + 0.5))
        if m is None or len(pr) != 2 or None in pr:
            continue
        n += 1
        first += abs(pr[0] - m) < abs(pr[1] - m)
    if n >= 5 and first / n >= 0.9:
        return "OVER_FIRST", n, first
    if n >= 5 and first / n <= 0.1:
        return "UNDER_FIRST", n, first
    return "UNVERIFIED", n, first


def label_sides(rows):
    """Label each paired price OVER/UNDER. With PROP_SIDE_ORDER=AUTO the order is
    proven from this board's own ladder prices; if it can't be proven, sides stay
    'A'/'B' and the engine uses lines only (no prices)."""
    order = SIDE_ORDER
    n = first = None
    if order == "AUTO":
        order, n, first = verify_side_order(rows)
    seen = {}
    for r in rows:
        tn = r["type_name"] or ""
        if "Milestone" in tn or "Double" in tn:
            r["side"] = "OVER"          # one-sided markets
            continue
        if r["line"] is None:
            r["side"] = None
            continue
        k = (r["athlete_id"], r["type_id"], r["line"])
        i = seen.get(k, 0)
        seen[k] = i + 1
        if order == "OVER_FIRST":
            r["side"] = "OVER" if i == 0 else "UNDER"
        elif order == "UNDER_FIRST":
            r["side"] = "UNDER" if i == 0 else "OVER"
        else:
            r["side"] = "A" if i == 0 else "B"
    return dict(side_order=order, side_checks=n, side_over_first=first)


# ---------- injuries ----------
def parse_injuries(js):
    rows = []
    for team in js.get("injuries", []):
        for inj in team.get("injuries", []):
            ath = inj.get("athlete", {})
            aid = ath.get("id")
            if aid is None:
                for l in ath.get("links", []):
                    m = re.search(r"/id/(\d+)", l.get("href", ""))
                    if m:
                        aid = m.group(1)
                        break
            rows.append(dict(team=team.get("displayName"), athlete_id=aid,
                             name=ath.get("displayName"), status=inj.get("status"),
                             detail=inj.get("shortComment"), date=inj.get("date")))
    return rows


def write_csv(path, rows):
    if not rows:
        open(path, "w").close()
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", day)
    raw = os.path.join(out, "raw")
    os.makedirs(raw, exist_ok=True)
    pulled = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    sb = get_json(f"{SITE}/scoreboard?dates={day.replace('-', '')}")
    save(os.path.join(raw, "scoreboard.json"), sb)
    games = parse_games(sb)
    write_csv(os.path.join(out, "games.csv"), games)

    all_props, checks = [], []
    for g in games:
        try:
            rows, chk = fetch_props(g["event_id"], raw)
        except Exception as e:
            rows, chk = None, dict(event_id=g["event_id"], ok=False, error=str(e))
        chk["matchup"] = f'{g["away"]}@{g["home"]}'
        checks.append(chk)
        if rows:
            all_props.extend(rows)
    write_csv(os.path.join(out, "props.csv"), all_props)

    try:
        inj = get_json(f"{SITE}/injuries")
        save(os.path.join(raw, "injuries.json"), inj)
        injuries = parse_injuries(inj)
    except Exception as e:
        injuries = []
        checks.append(dict(injuries_error=str(e)))
    write_csv(os.path.join(out, "injuries.csv"), injuries)

    manifest = dict(date=day, pulled_utc=pulled, side_order_mode=SIDE_ORDER, games=len(games),
                    games_with_lines=sum(g["total"] is not None for g in games),
                    prop_rows=len(all_props), injuries=len(injuries),
                    boards_ok=sum(1 for c in checks if c.get("ok")),
                    boards_failed=[c.get("matchup") for c in checks if "matchup" in c and not c.get("ok")],
                    checks=checks)
    save(os.path.join(out, "manifest.json"), manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != "checks"}, indent=1))
    # A failed board is reported, not fatal: the engine runs line-free for that game.


if __name__ == "__main__":
    main()
