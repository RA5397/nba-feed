#!/usr/bin/env python3
"""Probe ESPN's casino ids for NFL prop PRICES.

THE PROBLEM
ESPN's NFL propBets items for casino 100 (DraftKings) carry only the line:

    {"type": {...}, "current": {"target": {"value": 221.5}},
                    "open":    {"target": {"value": 225.5}}}

no `odds` block at all. The NBA feed hits the IDENTICAL endpoint path and gets prices on
every row (american + american_open, both sides). Both fetchers already read
`odds.american.value`, so this is a source-side gap, not a parsing one.

WHY IT MATTERS
Without prices every posted line can only be read as a -110 median. With prices, the ~2,900
"Milestones" rows per pull -- which are alternate thresholds on the same players -- become
points on the market's cumulative distribution. That is the difference between comparing the
model's median to the market's median, and comparing the whole simulated shape to the
market's. Mispricings live in the shape, not the midpoint.

WHAT THIS DOES
Walks the casino ids ESPN exposes, pulls one page of NFL propBets from each, and reports how
many sampled items carry a price. If any id returns prices, point the fetcher at it:
`DK = <id>` in feed/fetch_daily.py, or set NFL_CASINO for this repo's NFL job. The fetcher
needs no other change -- the parse and the A/B side labelling are already in place.

This could not be run from the environment it was written in (no route to ESPN), so it is
written to fail soft: an unreachable or non-JSON id is reported, never raised.

USAGE
    python -I nfl/probe_books.py                      # auto-pick a scheduled event
    python -I nfl/probe_books.py 401872995            # probe a specific event id
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "feed"))
import fetch_daily as fd

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
UA = {"User-Agent": "Mozilla/5.0 (nfl-feed probe)"}

# ESPN casino ids seen in the wild. 100 is DraftKings (what the feed uses today).
CANDIDATES = [100, 1002, 2000, 58, 31, 41, 36, 25, 38, 45, 2001, 2004, 19]


def soft_get(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except Exception as e:
        return None, str(e)[:120]


def pick_event():
    for off in range(0, 8):
        import datetime as dt
        day = dt.date.today() + dt.timedelta(days=off)
        js, err = soft_get(f"{SITE}/scoreboard?dates={day:%Y%m%d}")
        if not js:
            continue
        for ev in js.get("events", []):
            return ev["id"], ev.get("shortName")
    return None, None


def casino_name(cid):
    js, _ = soft_get(f"{CORE}/casinos/{cid}")
    if not js:
        return "?"
    return js.get("name") or js.get("displayName") or "?"


def main():
    eid = sys.argv[1] if len(sys.argv) > 1 else None
    label = None
    if eid is None:
        eid, label = pick_event()
    if eid is None:
        print("could not reach ESPN to pick an event; pass an event id explicitly")
        return
    print(f"probing event {eid} {label or ''}\n")
    print(f"{'casino':>8} {'name':<18}{'count':>8}{'sampled':>9}{'priced':>8}  verdict")
    print("-" * 70)
    hits = []
    for cid in CANDIDATES:
        url = f"{CORE}/events/{eid}/competitions/{eid}/odds/{cid}/propBets?limit=10&page=1"
        js, err = soft_get(url)
        if js is None:
            print(f"{cid:>8} {'-':<18}{'-':>8}{'-':>9}{'-':>8}  unreachable/no json ({err})")
            continue
        items = js.get("items") or []
        if not items:
            print(f"{cid:>8} {casino_name(cid):<18}{js.get('count', 0):>8}{0:>9}{0:>8}  no prop items")
            continue
        n = sum(1 for it in items
                if ((it.get("odds") or {}).get("american") or {}).get("value") is not None)
        verdict = "PRICED -- use this id" if n else "lines only"
        if n:
            hits.append((cid, n, len(items)))
        print(f"{cid:>8} {casino_name(cid):<18}{js.get('count', 0):>8}{len(items):>9}{n:>8}  {verdict}")

    print()
    if hits:
        best = max(hits, key=lambda h: h[1] / max(h[2], 1))
        print(f"=> casino {best[0]} returns prices on {best[1]}/{best[2]} sampled items.")
        print(f"   Point the NFL job at it and the american / american_open columns populate")
        print(f"   with no other change. Re-run with that id and confirm two-sided pricing")
        print(f"   (side A/B) before trusting a de-vig.")
    else:
        print("=> no ESPN casino id returns NFL prop prices. Prices then need a different")
        print("   source (an odds API with player-prop coverage, or a book scrape). Until")
        print("   then every posted line must be treated as a -110 median, and milestone")
        print("   ladders cannot be used to reconstruct the market distribution.")


if __name__ == "__main__":
    main()
