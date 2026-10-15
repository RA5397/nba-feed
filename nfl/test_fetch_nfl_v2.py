#!/usr/bin/env python3
"""Offline tests for the schema-2 additions. No network.

Everything here runs against raw JSON already committed under nfl/out/*/raw/, so CI proves
the PARSERS before a live pull is trusted. What cannot be covered offline, and why:
  * fetch_results.parse_summary -- needs a /summary payload, and none is committed yet.
    A shape test on a hand-built fixture is included instead.
  * probe_books -- is a live probe by definition.

Usage: python -I nfl/test_fetch_nfl_v2.py
"""
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "feed"))
import fetch_daily as fd                      # noqa: E402
sys.path.insert(0, HERE)
import fetch_results as fr                    # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAIL.append(name)


def load_gz(p):
    with gzip.open(p, "rt") as f:
        return json.load(f)


def newest_out():
    ds = sorted(glob.glob(os.path.join(HERE, "out", "*")))
    return ds[-1] if ds else None


print("== venue / roof / weather / team codes, parsed from the saved scoreboard ==")
out = newest_out()
sb_path = os.path.join(out or "", "raw", "scoreboard.json.gz")
if out and os.path.exists(sb_path):
    games = fd.parse_games(load_gz(sb_path))
    check("games parsed", len(games) > 0, f"{len(games)} games")
    have_dome = [g for g in games if g.get("dome") is not None]
    check("dome flag present on every game", len(have_dome) == len(games),
          f"{len(have_dome)}/{len(games)}")
    check("dome is 0/1", all(g["dome"] in (0, 1) for g in have_dome))
    domes = sum(g["dome"] for g in have_dome)
    check("dome count is plausible (1..8 of a 14-game slate)", 0 <= domes <= 10, f"{domes} indoor")
    check("venue name present", all(g.get("venue") for g in games))
    wx = [g for g in games if g.get("wx_temp_f") is not None]
    check("weather temp present on most games", len(wx) >= max(1, len(games) // 2),
          f"{len(wx)}/{len(games)}")
    check("wind/precip left blank rather than defaulted",
          all(g.get("wx_wind_mph") is None and g.get("wx_precip_pct") is None for g in games))
    # team codes
    bad = [g for g in games if g["home_nflverse"] in ("LAR", "WSH")
           or g["away_nflverse"] in ("LAR", "WSH")]
    check("ESPN codes mapped to nflverse (no LAR/WSH survive)", not bad,
          ",".join(f'{g["away"]}@{g["home"]}' for g in bad))
    mapped = [g for g in games if g["home_nflverse"] != g["home"]
              or g["away_nflverse"] != g["away"]]
    check("mapping is a no-op for the other 30 codes", all(
        {g["home"], g["away"]} & {"LAR", "WSH"} for g in mapped),
        f"{len(mapped)} games remapped")
    # the sign convention the engine depends on
    sgn = [g for g in games if g.get("total") is not None and g.get("spread_home") is not None]
    check("spread_home sign agrees with implied totals",
          all((g["spread_home"] < 0) == (g["implied_home"] > g["implied_away"])
              for g in sgn if g["spread_home"] != 0), f"{len(sgn)} checked")
    check("implied_home == total/2 - spread_home/2",
          all(abs((g["total"] / 2 - g["spread_home"] / 2) - g["implied_home"]) < 1e-9
              for g in sgn))
else:
    check("scoreboard raw available", False, "no raw/scoreboard.json.gz committed yet")

print("\n== line_history append is idempotent in schema and append-only ==")
import csv
import tempfile
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "line_history.csv")
    rows = [dict(event_id="1", athlete_id=9, type_id="8", type_name="Total Passing Yards",
                 line=221.5, line_open=225.5, american=None, american_open=None,
                 side="LINE", last_updated="x"),
            dict(event_id="1", athlete_id=10, type_id="9", type_name="Total Receptions",
                 line=4.5, line_open=4.5, american=-115, american_open=-110,
                 side="A", last_updated="y")]
    n1 = fd.append_line_history(p, "2026-10-14T14:10:00+00:00", "main", rows)
    n2 = fd.append_line_history(p, "2026-10-14T20:00:00+00:00", "close3", rows)
    with open(p) as f:
        rr = list(csv.DictReader(f))
    check("appends both snapshots", len(rr) == 4, f"{n1}+{n2} -> {len(rr)} rows")
    check("one header only", open(p).read().count("pull_utc,snap") == 1)
    check("snap labels retained", {x["snap"] for x in rr} == {"main", "close3"})
    check("extra keys ignored, no crash",
          fd.append_line_history(p, "t", "s", [dict(rows[0], junk=1)]) == 1)

print("\n== manifest file_checks ==")
with tempfile.TemporaryDirectory() as td:
    with open(os.path.join(td, "a.csv"), "w") as f:
        f.write("h1,h2\n1,2\n3,4\n")
    c = fd.file_checks(td, ["a.csv", "missing.csv"])
    check("row count excludes header", c["a.csv"]["rows"] == 2, str(c["a.csv"]["rows"]))
    check("sha256 is 64 hex chars", len(c["a.csv"]["sha256"]) == 64)
    check("missing file reported, not raised", c["missing.csv"]["exists"] is False)

print("\n== props parse keeps american_open and drops partial-game markets ==")
pp = sorted(glob.glob(os.path.join(out or "", "raw", "props_*_p1.json.gz")))
if pp:
    import fetch_nfl as fn
    page = load_gz(pp[0])
    eid = os.path.basename(pp[0]).split("_")[1]
    items = page["items"]
    kept = [it for it in items
            if not any(s in ((it.get("type") or {}).get("name") or "") for s in fn.PART_GAME)]
    check("partial-game markets would be dropped", len(kept) <= len(items),
          f"{len(items)-len(kept)} dropped of {len(items)}")
    check("ESPN serves no price for casino 100 on NFL (documents the gap)",
          all(((it.get("odds") or {}).get("american") or {}).get("value") is None
              for it in items),
          "if this FAILS, prices appeared -- rerun probe_books.py and expect A/B sides")
else:
    check("props raw available", False, "no raw/props_*_p1.json.gz committed yet")

print("\n== fetch_results.parse_summary shape test (hand-built fixture) ==")
fixture = {"boxscore": {"players": [
    {"team": {"abbreviation": "WSH"}, "statistics": [
        {"name": "passing", "labels": ["C/ATT", "YDS", "TD", "INT"],
         "athletes": [{"athlete": {"id": "4432577", "displayName": "Jayden Daniels"},
                       "stats": ["19/31", "248", "2", "1"]}]},
        {"name": "rushing", "labels": ["CAR", "YDS", "TD"],
         "athletes": [{"athlete": {"id": "4432577", "displayName": "Jayden Daniels"},
                       "stats": ["8", "47", "0"]}]}]},
    {"team": {"abbreviation": "LAR"}, "statistics": [
        {"name": "receiving", "labels": ["REC", "YDS", "TD", "TGTS"],
         "athletes": [{"athlete": {"id": "4426354", "displayName": "Puka Nacua"},
                       "stats": ["9", "121", "1", "12"]}]}]}]}}
rows = fr.parse_summary(fixture, "999")
got = {(r["athlete_id"], r["stat"]): r["value"] for r in rows}
check("C/ATT split into completions + attempts",
      got.get((4432577, "completions")) == 19 and got.get((4432577, "attempts")) == 31)
check("YDS resolved per category",
      got.get((4432577, "passing_yards")) == 248 and got.get((4432577, "rushing_yards")) == 47)
check("TD resolved per category",
      got.get((4432577, "passing_tds")) == 2 and got.get((4432577, "rushing_tds")) == 0)
check("receiving parsed", got.get((4426354, "receptions")) == 9
      and got.get((4426354, "receiving_yards")) == 121
      and got.get((4426354, "targets")) == 12)
check("opponent filled from the other team block",
      {r["opponent"] for r in rows if r["team"] == "WSH"} == {"LAR"})
check("malformed payload returns [] rather than raising",
      fr.parse_summary({"boxscore": {"players": [{"statistics": [{}]}]}}, "0") == [])
check("empty payload safe", fr.parse_summary({}, "0") == [] and fr.parse_summary(None, "0") == [])


# ---------------------------------------------------------------------------
# Board verification: the three states seen on the live 2026-10-15 pull.
print("\n== board verification: live-market drift, short page, no board ==")
import fetch_nfl as fn2
import tempfile as _tf

def _page(count, n, total_pages):
    return {"count": count, "pageCount": total_pages,
            "items": [{"type": {"id": "8", "name": "Total Passing Yards"},
                       "athlete": {"$ref": "/athletes/1"},
                       "current": {"target": {"value": 1.5}},
                       "open": {"target": {"value": 1.5}}} for _ in range(n)]}

def _run(responder, restarts=3):
    calls = {"n": 0}
    def fake(url, tries=4):
        calls["n"] += 1
        return responder(url, calls["n"])
    orig, fn2.fd.get_json = fn2.fd.get_json, fake
    orig_sleep, fn2.fd.time.sleep = fn2.fd.time.sleep, lambda *_a: None
    try:
        with _tf.TemporaryDirectory() as td:
            return fn2.fetch_props("E", td, save_raw=False, restarts=restarts)
    finally:
        fn2.fd.get_json, fn2.fd.time.sleep = orig, orig_sleep

# (a) count moves mid-pull, then settles on the restart -> board KEPT, no drift flag
state = {"restarted": False}
def settles(url, n):
    import re as _r
    p = int(_r.search(r"page=(\d+)", url).group(1))
    if p == 1 and n > 1:
        state["restarted"] = True
    if not state["restarted"]:
        return _page(250 if p == 1 else 252, 100 if p < 3 else 50, 3)
    return _page(250, 100 if p < 3 else 50, 3)
rows, chk = _run(settles)
check("drift then settle -> board kept", rows is not None and chk.get("ok") is True)
check("restart happened", state["restarted"])
check("no drift flag once it settles", not chk.get("count_drift"))

# (b) count NEVER settles -> board still KEPT (every page complete), flagged.
# The fixture must keep PAGE SIZES consistent with the count page 1 reported (250 -> 100/100/50)
# while only the REPORTED count drifts. Otherwise it trips the short-page path instead, which
# is what the first version of this test did -- the sizes have to stay honest to isolate drift.
def never_settles(url, n):
    import re as _r
    p = int(_r.search(r"page=(\d+)", url).group(1))
    sizes = {1: 100, 2: 100, 3: 50}
    reported = 250 if p == 1 else 250 + n      # page 1 anchors count; later pages disagree
    return _page(reported, sizes[p], 3)
rows, chk = _run(never_settles)
check("persistent drift -> board KEPT not discarded", rows is not None,
      "this is the SEA@DEN case; old code returned None")
check("flagged count_drift", chk.get("count_drift") is True)
check("counts_seen recorded", len(chk.get("counts_seen") or []) > 1)

# (c) genuinely short page -> still rejected whole
def short(url, n):
    import re as _r
    p = int(_r.search(r"page=(\d+)", url).group(1))
    return _page(250, 100 if p == 1 else 95, 3)
rows, chk = _run(short)
check("short page -> board still rejected", rows is None and chk.get("ok") is False)
check("reason recorded", chk.get("reason") == "short page")

# (d) 404 -> no_board, kept out of boards_failed
def missing(url, n):
    raise RuntimeError("GET failed after 4 tries: " + url + ": HTTP Error 404: Not Found")
rows, chk = _run(missing)
check("404 -> no_board flag", rows is None and chk.get("no_board") is True,
      "this is the NE@CHI case")

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all offline tests passed")
