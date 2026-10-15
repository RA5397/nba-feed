#!/usr/bin/env python3
"""Daily NFL feed: this week's games + DraftKings game lines + DraftKings player props + injuries.

Same integrity rules as fetch_daily.py (NBA): every propBets page is row-counted, short pages
are re-pulled, and a board that still fails is rejected whole (no partial boards).

NFL differences:
  * Pulls every game in the next 7 days (one NFL week), not one date.
  * ESPN's NFL prop items currently carry the LINE but no price. Each line appears twice
    (over/under slots); without prices the two are identical, so one row per line is kept.
    If prices appear later, both rows are kept with sides A/B (or OVER/UNDER once verified).
  * Quarter/half markets are dropped (full-game only) to keep the repo small.
  * Raw JSON is saved gzipped.

Outputs (nfl/out/<YYYY-MM-DD of pull>/): games.csv, props.csv, injuries.csv, manifest.json, raw/*.json.gz
Usage: python fetch_nfl.py [YYYY-MM-DD start date, default today ET]
"""
import datetime as dt
import gzip
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "feed"))
import fetch_daily as fd   # shared: get_json, parse_games, athlete_id, write_csv, parse_injuries

fd.SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
fd.CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
PAGE = 100
PART_GAME = ("1st Quarter", "2nd Quarter", "3rd Quarter", "4th Quarter", "1st Half", "2nd Half")


def save_gz(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt") as f:
        json.dump(obj, f)


def fetch_window(d0, days):
    """ESPN's NFL scoreboard rejects date ranges (verified Oct 14: '?dates=A-B' errors), so pull
    one date at a time and merge events by id."""
    events, seen = [], set()
    for k in range(days):
        day = d0 + dt.timedelta(days=k)
        sb = fd.get_json(f"{fd.SITE}/scoreboard?dates={day:%Y%m%d}")
        for ev in sb.get("events", []):
            if ev["id"] not in seen:
                seen.add(ev["id"]); events.append(ev)
    return {"events": events}


def fetch_props(event_id, rawdir, save_raw=True):
    base = f"{fd.CORE}/events/{event_id}/competitions/{event_id}/odds/{fd.DK}/propBets"
    first = fd.get_json(f"{base}?limit={PAGE}&page=1")
    count, pages = first.get("count", 0), first.get("pageCount", 0)
    items, checks = [], []
    for p in range(1, pages + 1):
        expect = min(PAGE, count - (p - 1) * PAGE)
        page = first if p == 1 else None
        got = 0
        for _ in range(3):
            if page is None:
                page = fd.get_json(f"{base}?limit={PAGE}&page={p}")
            got = len(page.get("items", []))
            if got == expect and page.get("count") == count:
                break
            page = None
            fd.time.sleep(2)
        ok = page is not None
        checks.append(dict(page=p, expected=expect, received=got, ok=ok))
        if not ok:
            return None, dict(event_id=event_id, count=count, pages=pages, ok=False, pages_detail=checks)
        if save_raw:
            save_gz(os.path.join(rawdir, f"props_{event_id}_p{p}.json.gz"), page)
        items.extend(page["items"])
    if len(items) != count:
        return None, dict(event_id=event_id, count=count, received=len(items), ok=False, pages_detail=checks)
    rows, seen = [], {}
    for i, it in enumerate(items):
        tn = (it.get("type") or {}).get("name") or ""
        if any(s in tn for s in PART_GAME):
            continue
        o = it.get("odds") or {}
        am = (o.get("american") or {}).get("value")
        r = dict(event_id=event_id, idx=i,
                 athlete_id=fd.athlete_id((it.get("athlete") or {}).get("$ref")),
                 type_id=(it.get("type") or {}).get("id"), type_name=tn.replace(" (incl. overtime)", ""),
                 line=((it.get("current") or {}).get("target") or {}).get("value"),
                 line_open=((it.get("open") or {}).get("target") or {}).get("value"),
                 american=am,
                 # ESPN serves no price on NFL prop items for casino 100 today (verified:
                 # items carry only current/open target.value). The NBA feed on the SAME
                 # endpoint returns prices on every row, so this is a source-side gap, not
                 # a parsing one. Captured anyway -- the NBA fetcher already does -- so the
                 # column is populated the moment ESPN starts serving it. See probe_books.py.
                 american_open=(o.get("american") or {}).get("open"),
                 last_updated=it.get("lastUpdated"))
        k = (r["athlete_id"], r["type_id"], r["line"])
        n = seen.get(k, 0); seen[k] = n + 1
        if am is None:
            if n > 0:
                continue                      # unpriced duplicate of the same line
            r["side"] = "LINE"
        else:
            r["side"] = "OVER" if "Milestone" in tn else ("A" if n == 0 else "B")
        rows.append(r)
    return rows, dict(event_id=event_id, count=count, pages=pages, received=len(items), ok=True,
                      kept_full_game_rows=len(rows), pages_detail=checks)


def main():
    # SNAP distinguishes repeat pulls on the same date. "main" behaves exactly as before.
    # Any other label (close1/close2/close3) writes ONLY games_<snap>.csv / props_<snap>.csv
    # and skips injuries and raw JSON, so a pre-kickoff refresh is cheap and never
    # overwrites the morning board. Every snapshot appends to nfl/line_history.csv.
    snap = os.environ.get("SNAP", "main").strip() or "main"
    args = [a for a in sys.argv[1:] if a]
    start = args[0] if args else dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    d0 = dt.date.fromisoformat(start)
    d1 = d0 + dt.timedelta(days=7)          # window = d0 .. d0+7 (8 dates)
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "out", start)
    raw = os.path.join(out, "raw")
    os.makedirs(raw, exist_ok=True)
    pulled = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    is_main = (snap == "main")
    sfx = "" if is_main else f"_{snap}"

    sb = fetch_window(d0, 8)
    if is_main:
        save_gz(os.path.join(raw, "scoreboard.json.gz"), sb)
    games = [g for g in fd.parse_games(sb) if g["status"] == "STATUS_SCHEDULED"]
    fd.write_csv(os.path.join(out, f"games{sfx}.csv"), games)

    all_props, checks = [], []
    for g in games:
        try:
            rows, chk = fetch_props(g["event_id"], raw, save_raw=is_main)
        except Exception as e:
            rows, chk = None, dict(event_id=g["event_id"], ok=False, error=str(e))
        chk["matchup"] = f'{g["away"]}@{g["home"]}'
        checks.append(chk)
        if rows:
            all_props.extend(rows)
    fd.write_csv(os.path.join(out, f"props{sfx}.csv"), all_props)

    # append-only line time series: open -> intraday -> close for every prop
    hist = os.path.join(here, "line_history.csv")
    n_hist = fd.append_line_history(hist, pulled, snap, all_props)

    injuries = []
    if is_main:
        try:
            inj = fd.get_json(f"{fd.SITE}/injuries")
            save_gz(os.path.join(raw, "injuries.json.gz"), inj)
            injuries = fd.parse_injuries(inj)
        except Exception as e:
            checks.append(dict(injuries_error=str(e)))
        fd.write_csv(os.path.join(out, "injuries.csv"), injuries)

    names = [f"games{sfx}.csv", f"props{sfx}.csv"] + (["injuries.csv"] if is_main else [])
    priced = sum(1 for r in all_props if r.get("american") is not None)
    manifest = dict(sport="nfl", snap=snap, schema=2, window=f"{d0}..{d1}", pulled_utc=pulled,
                    games=len(games),
                    games_with_lines=sum(g["total"] is not None for g in games),
                    games_with_dome=sum(g.get("dome") is not None for g in games),
                    prop_rows=len(all_props), priced_prop_rows=priced, injuries=len(injuries),
                    line_history_appended=n_hist,
                    boards_ok=sum(1 for c in checks if c.get("ok")),
                    boards_failed=[c.get("matchup") for c in checks if "matchup" in c and not c.get("ok")],
                    files=fd.file_checks(out, names),
                    checks=checks)
    with open(os.path.join(out, f"manifest{sfx}.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps({k: v for k, v in manifest.items() if k != "checks"}, indent=1))


if __name__ == "__main__":
    main()
