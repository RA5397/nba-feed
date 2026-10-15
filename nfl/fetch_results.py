#!/usr/bin/env python3
"""NFL results: actual player stat lines for completed games, keyed on ESPN athlete_id.

WHY THIS EXISTS
The lines in props.csv are keyed on ESPN athlete_id. Grading them previously meant joining
to a separate stats source by NAME, and name matching has broken in both directions:
  * too loose -- a last-name fallback matched "DJ Moore" to a different Moore with 0 stats
    and "Josh Allen" to Josh Hines-Allen, charging two losses that never happened and
    reporting a weekly grade of 68.9% when the true figure was 73.7%
  * too strict -- an exact dict lookup never matched "James Cook III" against "James Cook",
    so a tag silently never fired for the #3 rusher in the league
Writing results on the SAME ids as the lines removes the join entirely, and makes the repo
a self-contained backtest set: posted line -> closing line -> actual, all on athlete_id.

OUTPUT (nfl/out/<date>/results.csv) -- long format, one row per athlete-stat:
    event_id, athlete_id, name, team, opponent, category, stat, value
Long rather than wide on purpose: ESPN's stat labels differ per category and have changed
before. A long file cannot silently mis-assign a column if the label order shifts; pivot on
(athlete_id, stat) at the consumer end. STAT_MAP below gives the canonical names the engine
uses, and anything unrecognised is still written under its raw ESPN label rather than dropped.

USAGE
    python -I nfl/fetch_results.py                 # all finished games in the last 4 days
    python -I nfl/fetch_results.py 2026-10-12      # all finished games on that date
    python -I nfl/fetch_results.py 2026-10-12 7    # that date plus the next 6
"""
import datetime as dt
import gzip
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "feed"))
import fetch_daily as fd

fd.SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
fd.CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"

FINISHED = ("STATUS_FINAL", "STATUS_FULL_TIME")

# ESPN boxscore label -> canonical engine stat name. Labels are matched case-insensitively
# after stripping spaces. Unmapped labels are kept under their raw name.
STAT_MAP = {
    # passing: ESPN gives "C/ATT" as a single "19/31" string, split out below
    "YDS": None,            # ambiguous on its own -- resolved per category
    "COMPLETIONS": "completions", "PASSINGATTEMPTS": "attempts",
    "TD": None, "INT": "passing_interceptions", "SACKS": "sacks_suffered",
    "CAR": "carries", "REC": "receptions", "TGTS": "targets", "TARGETS": "targets",
    "LONG": None, "AVG": None, "FUM": "fumbles", "LOST": "fumbles_lost",
}
CAT_YDS = {"passing": "passing_yards", "rushing": "rushing_yards", "receiving": "receiving_yards"}
CAT_TD = {"passing": "passing_tds", "rushing": "rushing_tds", "receiving": "receiving_tds"}


def canon(cat, label):
    k = (label or "").upper().replace(" ", "")
    if k == "YDS":
        return CAT_YDS.get(cat, f"{cat}_yards")
    if k == "TD":
        return CAT_TD.get(cat, f"{cat}_tds")
    return STAT_MAP.get(k) or (label or "").strip().lower().replace(" ", "_")


def num(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_summary(js, event_id):
    """Pull every athlete stat line out of an ESPN /summary payload.

    Defensive by construction: this runs against a live endpoint that cannot be tested from
    a sandbox, so every level is a .get() and a shape that does not match is skipped rather
    than raising. A missing category costs rows, never the whole pull.
    """
    rows = []
    box = (js or {}).get("boxscore") or {}
    teams = box.get("players") or []
    abbrs = []
    for t in teams:
        abbrs.append(((t.get("team") or {}).get("abbreviation")) or "")
    for ti, t in enumerate(teams):
        team = abbrs[ti] or None
        opp = abbrs[1 - ti] if len(abbrs) == 2 else None
        for grp in t.get("statistics") or []:
            cat = (grp.get("name") or grp.get("text") or "").strip().lower()
            labels = grp.get("labels") or grp.get("keys") or []
            for ath in grp.get("athletes") or []:
                a = ath.get("athlete") or {}
                aid = a.get("id")
                if aid is None:
                    aid = fd.athlete_id((a.get("$ref") or ""))
                if aid is None:
                    continue
                vals = ath.get("stats") or []
                for li, lab in enumerate(labels):
                    if li >= len(vals):
                        break
                    raw = vals[li]
                    # ESPN packs completions/attempts as one "19/31" cell
                    if (lab or "").upper().replace(" ", "") in ("C/ATT", "C-ATT") and "/" in str(raw):
                        c, _, at = str(raw).partition("/")
                        for nm, vv in (("completions", c), ("attempts", at)):
                            if num(vv) is not None:
                                rows.append(dict(event_id=event_id, athlete_id=int(aid),
                                                 name=a.get("displayName"), team=team,
                                                 opponent=opp, category=cat, stat=nm,
                                                 value=num(vv)))
                        continue
                    v = num(raw)
                    if v is None:
                        continue
                    rows.append(dict(event_id=event_id, athlete_id=int(aid),
                                     name=a.get("displayName"), team=team, opponent=opp,
                                     category=cat, stat=canon(cat, lab), value=v))
    return rows


def main():
    args = [a for a in sys.argv[1:] if a]
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    if args:
        d0 = dt.date.fromisoformat(args[0])
        days = int(args[1]) if len(args) > 1 else 1
    else:
        d0, days = today - dt.timedelta(days=4), 5      # catch Thu/Sun/Mon just played

    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "out", d0.isoformat())
    raw = os.path.join(out, "raw")
    os.makedirs(raw, exist_ok=True)
    pulled = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    events, seen = [], set()
    for k in range(days):
        day = d0 + dt.timedelta(days=k)
        sb = fd.get_json(f"{fd.SITE}/scoreboard?dates={day:%Y%m%d}")
        for ev in sb.get("events", []):
            st = (ev.get("status") or {}).get("type") or {}
            if ev["id"] in seen:
                continue
            if st.get("name") in FINISHED or st.get("completed") is True:
                seen.add(ev["id"])
                comp = (ev.get("competitions") or [{}])[0]
                tm = {c.get("homeAway"): c for c in comp.get("competitors") or []}
                events.append(dict(
                    event_id=ev["id"], date=ev.get("date"),
                    home=((tm.get("home") or {}).get("team") or {}).get("abbreviation"),
                    away=((tm.get("away") or {}).get("team") or {}).get("abbreviation")))

    rows, checks = [], []
    for ev in events:
        eid = ev["event_id"]
        try:
            js = fd.get_json(f"{fd.SITE}/summary?event={eid}")
            with gzip.open(os.path.join(raw, f"summary_{eid}.json.gz"), "wt") as f:
                json.dump(js, f)
            r = parse_summary(js, eid)
            ok = len(r) > 0
            rows.extend(r)
        except Exception as e:
            r, ok = [], False
            checks.append(dict(event_id=eid, error=str(e)))
        checks.append(dict(event_id=eid, matchup=f'{ev["away"]}@{ev["home"]}',
                           rows=len(r), athletes=len({x["athlete_id"] for x in r}), ok=ok))

    # nflverse team codes alongside ESPN's, same two exceptions as games.csv
    for r in rows:
        r["team_nflverse"] = fd.ESPN2NFLVERSE.get(r["team"], r["team"])
        r["opponent_nflverse"] = fd.ESPN2NFLVERSE.get(r["opponent"], r["opponent"])

    fd.write_csv(os.path.join(out, "results.csv"), rows)
    man = dict(sport="nfl", kind="results", schema=1, pulled_utc=pulled,
               window=f"{d0}..{d0 + dt.timedelta(days=days - 1)}",
               finished_games=len(events), rows=len(rows),
               athletes=len({r["athlete_id"] for r in rows}),
               games_failed=[c.get("matchup") for c in checks
                             if "matchup" in c and not c.get("ok")],
               files=fd.file_checks(out, ["results.csv"]), checks=checks)
    with open(os.path.join(out, "manifest_results.json"), "w") as f:
        json.dump(man, f, indent=1)
    print(json.dumps({k: v for k, v in man.items() if k != "checks"}, indent=1))


if __name__ == "__main__":
    main()
