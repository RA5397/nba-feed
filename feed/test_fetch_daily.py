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

