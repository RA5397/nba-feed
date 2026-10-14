"""Offline tests for fetch_daily.py (no network). Run: python3 -I feed/test_fetch_daily.py"""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_daily as fd

# Two real items captured verbatim from ESPN on 2026-10-14 (BOS@DET, Tatum points 26.5)
ITEM = json.loads('''{"competition":{"$ref":"http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/401909088/competitions/401909088?lang=en&region=us"},"athlete":{"$ref":"http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2027/athletes/4065648?lang=en&region=us"},"provider":{"$ref":"http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/casinos/100?lang=en&region=us"},"type":{"id":"1","name":"Total Points"},"odds":{"american":{"value":"-118","open":"-118"},"decimal":{"value":"1.84","open":"1.84"},"fraction":{"value":"50/59","open":"50/59"},"total":{"value":"26.5","open":"26.5"}},"lastUpdated":"2026-10-14T00:05Z","current":{"target":{"value":26.5,"displayValue":"26.5"}},"open":{"target":{"value":26.5,"displayValue":"26.5"}}}''')
ITEM2 = copy.deepcopy(ITEM); ITEM2["odds"]["american"]["value"] = "-108"


def fake_pages(count, short_page=None):
    items = []
    for i in range(count):
        it = copy.deepcopy(ITEM if i % 2 == 0 else ITEM2)
        it["current"]["target"]["value"] = 10.5 + (i // 2)   # distinct market every 2 items
        items.append(it)
    pages = (count + fd.PAGE - 1) // fd.PAGE

    def get_json(url, tries=4):
        p = int(url.split("page=")[1])
        chunk = items[(p - 1) * fd.PAGE: p * fd.PAGE]
        if short_page == p:
            chunk = chunk[:-5]                       # the drop we saw on Oct 14
        return {"count": count, "pageIndex": p, "pageSize": fd.PAGE, "pageCount": pages, "items": chunk}
    return get_json


def test_full_board():
    fd.get_json = fake_pages(415)
    fd.time.sleep = lambda s: None
    with tempfile.TemporaryDirectory() as d:
        rows, chk = fd.fetch_props("401909088", d)
    assert chk["ok"] and len(rows) == 415 and chk["pages"] == 5, chk
    r0 = rows[0]
    assert r0["athlete_id"] == 4065648 and r0["type_name"] == "Total Points" and r0["american"] == "-118"
    assert {r["side"] for r in rows} == {"A", "B"} and chk["side_order"] == "UNVERIFIED"
    print("PASS full board: 415/415 rows, 5 pages, athlete id parsed; no ladder -> sides stay A/B")


def test_short_page_rejected():
    fd.get_json = fake_pages(415, short_page=3)
    with tempfile.TemporaryDirectory() as d:
        rows, chk = fd.fetch_props("401909088", d)
    assert rows is None and not chk["ok"], chk
    bad = [p for p in chk["pages_detail"] if not p["ok"]][0]
    assert bad["page"] == 3 and bad["expected"] == 100 and bad["received"] == 95
    print("PASS short page: page 3 got 95/100 after 3 retries -> board rejected, no partial write")


def test_games_parse():
    sb = {"events": [{"id": "401909088", "date": "2026-10-20T19:00Z",
           "status": {"type": {"name": "STATUS_SCHEDULED"}},
           "competitions": [{"competitors": [
               {"homeAway": "home", "team": {"id": "8", "abbreviation": "DET"}},
               {"homeAway": "away", "team": {"id": "2", "abbreviation": "BOS"}}],
               "odds": [{"provider": {"id": "100", "name": "DraftKings"}, "details": "DET -1.5",
                         "overUnder": 222.5, "spread": -1.5,
                         "homeTeamOdds": {"moneyLine": -125}, "awayTeamOdds": {"moneyLine": 105}}]}]}]}
    g = fd.parse_games(sb)[0]
    assert g["spread_home"] == -1.5 and g["total"] == 222.5
    assert abs(g["implied_home"] - 112.0) < 1e-9 and abs(g["implied_away"] - 110.5) < 1e-9
    # away favorite: details names the away team -> spread_home must flip to +
    sb["events"][0]["competitions"][0]["odds"][0]["details"] = "BOS -3.5"
    sb["events"][0]["competitions"][0]["odds"][0]["spread"] = 3.5
    g = fd.parse_games(sb)[0]
    assert g["spread_home"] == 3.5 and g["implied_away"] > g["implied_home"]
    print("PASS games: DET -1.5 / 222.5 -> DET 112.0, BOS 110.5; away-favorite sign handled")


def test_side_order_from_ladder():
    """Real Oct 14 BOS@DET prices: paired lines + matching X+1 ladder rungs."""
    pairs = {(3078576,"1",14.5):("-123","-104"),(4065648,"1",26.5):("-118","-108"),(4432166,"1",23.5):("-118","-108"),
             (4433621,"1",15.5):("-105","-121"),(4684742,"1",10.5):("+106","-136"),(4251,"2",3.5):("+130","-171"),
             (3078576,"2",3.5):("-158","+119"),(4065648,"2",5.5):("-153","+116"),(4251,"3",3.5):("-145","+110"),
             (4065648,"3",8.5):("-124","-106")}
    ladder = {(3078576,"218",15):"-123",(4065648,"218",27):"-120",(4432166,"218",24):"-118",(4433621,"218",16):"-104",
              (4684742,"218",11):"+107",(4251,"219",4):"+130",(3078576,"219",4):"-159",(4065648,"219",6):"-152",
              (4251,"220",4):"-145",(4065648,"220",9):"-122"}
    def mk(aid, t, ln, am, tn):
        return dict(athlete_id=aid, type_id=t, type_name=tn, line=ln, american=am)
    rows = []
    for (aid, t, ln), (a, b) in pairs.items():
        rows += [mk(aid, t, ln, a, "Total X"), mk(aid, t, ln, b, "Total X")]
    for (aid, t, ln), a in ladder.items():
        rows.append(mk(aid, t, ln, a, "Points Milestones"))
    fd.SIDE_ORDER = "AUTO"
    res = fd.label_sides(rows)
    assert res["side_order"] == "OVER_FIRST" and res["side_checks"] == 10 and res["side_over_first"] == 10, res
    assert rows[0]["side"] == "OVER" and rows[1]["side"] == "UNDER"
    # flipped feed must be detected as UNDER_FIRST, not silently mislabeled
    flipped = []
    for (aid, t, ln), (a, b) in pairs.items():
        flipped += [mk(aid, t, ln, b, "Total X"), mk(aid, t, ln, a, "Total X")]
    flipped += [r for r in rows if "Milestone" in r["type_name"]]
    assert fd.label_sides(flipped)["side_order"] == "UNDER_FIRST"
    print("PASS side order: real Oct 14 prices -> OVER_FIRST 10/10; a flipped feed is caught as UNDER_FIRST")


if __name__ == "__main__":
    test_full_board(); test_short_page_rejected(); test_games_parse(); test_side_order_from_ladder()
    print("ALL TESTS PASS")
