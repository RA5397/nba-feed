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
