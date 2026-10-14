"""Offline tests for fetch_nfl.py. Run: python3 -I nfl/test_fetch_nfl.py"""
import copy, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_nfl as fn
fd = fn.fd

# real item captured verbatim from ESPN 2026-10-14 (SEA@DEN, passing yards, no price)
ITEM = json.loads('{"competition":{"$ref":"http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/401872995/competitions/401872995?lang=en&region=us"},"athlete":{"$ref":"http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/athletes/3912547?lang=en&region=us"},"provider":{"$ref":"http://sports.core.api.espn.com/v2/sports/football/leagues/nfl/casinos/100?lang=en&region=us"},"type":{"id":"8","name":"Total Passing Yards (incl. overtime)"},"lastUpdated":"2026-10-14T05:23Z","current":{"target":{"value":221.5,"displayValue":"221.5"}},"open":{"target":{"value":225.5,"displayValue":"225.5"}}}')


def pages(count, short=None, quarter_every=0):
    items = []
    for i in range(count):
        it = copy.deepcopy(ITEM)
        it["current"]["target"]["value"] = 100.5 + i // 2          # each line appears twice
        if quarter_every and i % quarter_every == 0:
            it["type"] = {"id": "213", "name": "Receiving Yards Milestones - 1st Quarter"}
        items.append(it)
    n = (count + fn.PAGE - 1) // fn.PAGE
    def get(url, tries=4):
        p = int(url.split("page=")[1]); ch = items[(p - 1) * fn.PAGE: p * fn.PAGE]
        if short == p: ch = ch[:-3]
        return {"count": count, "pageCount": n, "items": ch}
    return get


fd.time.sleep = lambda s: None
fd.get_json = pages(1375)
with tempfile.TemporaryDirectory() as d:
    rows, chk = fn.fetch_props("401872995", d)
    assert chk["ok"] and chk["received"] == 1375 and chk["pages"] == 14
    assert len(rows) == 688 and all(r["side"] == "LINE" for r in rows)       # 1375 items -> 688 unique lines
    assert rows[0]["athlete_id"] == 3912547 and rows[0]["type_name"] == "Total Passing Yards" and rows[0]["line"] == 100.5
    assert os.path.exists(os.path.join(d, "props_401872995_p14.json.gz"))
print("PASS full NFL board: 1375/1375 items, 14 pages, unpriced pairs collapsed to 688 lines, raw gzipped")

fd.get_json = pages(1375, short=9)
with tempfile.TemporaryDirectory() as d:
    rows, chk = fn.fetch_props("401872995", d)
    assert rows is None and not chk["ok"]
print("PASS short page 9 -> board rejected, no partial write")

fd.get_json = pages(400, quarter_every=4)
with tempfile.TemporaryDirectory() as d:
    rows, chk = fn.fetch_props("401872995", d)
    assert all("Quarter" not in r["type_name"] for r in rows)
print("PASS quarter/half markets dropped")

sb = {"events": [{"id": "401872995", "date": "2026-10-16T00:15Z", "status": {"type": {"name": "STATUS_SCHEDULED"}},
       "competitions": [{"competitors": [{"homeAway": "home", "team": {"id": "7", "abbreviation": "DEN"}},
                                         {"homeAway": "away", "team": {"id": "26", "abbreviation": "SEA"}}],
                         "odds": [{"provider": {"id": "100", "name": "DraftKings"}, "details": "SEA -2.5", "overUnder": 43.5, "spread": 2.5}]}]}]}
g = fd.parse_games(sb)[0]
assert g["spread_home"] == 2.5 and g["implied_away"] == 23.0 and g["implied_home"] == 20.5
print("PASS NFL game line: SEA -2.5 / 43.5 -> SEA 23.0, DEN 20.5 (road favorite sign handled)")
import datetime as dt
calls = []
def sb_get(url, tries=4):
    calls.append(url)
    assert "-" not in url.split("dates=")[1], "date ranges are rejected by ESPN NFL"
    day = url.split("dates=")[1]
    ev = {"id": "401872995"} if day in ("20261015",) else {"id": "X" + day}
    return {"events": [ev, {"id": "401872995"}]}     # duplicate across days must be merged
fd.get_json = sb_get
w = fn.fetch_window(dt.date(2026, 10, 14), 8)
assert len(calls) == 8 and len({e["id"] for e in w["events"]}) == len(w["events"])
print(f"PASS week window: 8 single-date calls, {len(w['events'])} unique events, no date-range URL")
print("ALL NFL TESTS PASS")
