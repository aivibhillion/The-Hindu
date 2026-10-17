#!/usr/bin/env python3
"""
build-index.py  —  VibhillionAI · The Hindu viewer

Scans the repo for daily edition pages laid out as

    <YEAR>/<Month>/<DD>.html        e.g.  2026/October/15.html

reads the articles out of each page (<article class="ca-article" ...>) and writes
content-index.json, which index.html fetches on load.

Run by the GitHub Pages workflow on every push, so nothing needs to be run by hand.
Standard library only (no pip install).
"""
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "content-index.json"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTH_BY_LOWER = {m.lower(): m for m in MONTHS}


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


class DayParser(HTMLParser):
    """Collects <title>, <h1> and every <article class="ca-article"> on a day page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.articles = []
        self._cur = None
        self._depth = 0          # nested <article> depth inside the current article
        self._cap = None         # what we are currently capturing text for
        self._buf = []
        self._in_title = False
        self._in_h1 = False
        self._facts_depth = 0    # >0 while inside <ul class="key-facts">

    # ---- helpers
    def _start_cap(self, what):
        self._cap, self._buf = what, []

    def _end_cap(self):
        text = clean("".join(self._buf))
        what, self._cap, self._buf = self._cap, None, []
        a = self._cur
        if a is None or not text:
            return
        if what == "headline" and not a["headline"]:
            a["headline"] = text
        elif what == "summary" and not a["summary"]:
            a["summary"] = text
        elif what == "fact":
            a["facts"].append(text)

    # ---- parser callbacks
    def handle_starttag(self, tag, attrs):
        at = dict(attrs)
        cls = (at.get("class") or "").split()
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and self._cur is None:
            self._in_h1 = True
        elif tag == "article":
            if self._cur is not None:
                self._depth += 1
            elif "ca-article" in cls:
                read = at.get("data-read") or ""
                self._cur = {
                    "id": at.get("id") or "",
                    "category": clean(at.get("data-category")) or "National",
                    "relevance": clean(at.get("data-relevance")),
                    "readMin": int(read) if read.strip().isdigit() else 0,
                    "headline": "", "summary": "", "facts": [],
                }
                self._depth = 0
        elif self._cur is not None:
            if tag == "h2" and not self._cur["headline"]:
                self._start_cap("headline")
            elif tag == "p" and "summary" in cls and not self._cur["summary"]:
                self._start_cap("summary")
            elif tag == "ul" and "key-facts" in cls:
                self._facts_depth = 1
            elif tag == "ul" and self._facts_depth:
                self._facts_depth += 1
            elif tag == "li" and self._facts_depth == 1:
                self._start_cap("fact")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "article" and self._cur is not None:
            if self._depth:
                self._depth -= 1
            else:
                self.articles.append(self._cur)
                self._cur, self._cap, self._facts_depth = None, None, 0
        elif self._cur is not None:
            if tag in ("h2", "p", "li") and self._cap:
                self._end_cap()
            elif tag == "ul" and self._facts_depth:
                self._facts_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_h1:
            self.h1 += data
        if self._cap:
            self._buf.append(data)


def parse_day(path):
    parser = DayParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    arts = parser.articles
    notes = []
    for i, a in enumerate(arts, 1):
        if not a["id"]:
            a["id"] = f"a{i}"
            notes.append(f"article {i} has no id attribute, using a{i}")
        if not a["headline"]:
            a["headline"] = f"Article {i}"
            notes.append(f"article {i} has no <h2> headline")
        if not a["readMin"]:
            words = len(" ".join([a["headline"], a["summary"]] + a["facts"]).split())
            a["readMin"] = max(1, round(words / 40))
    if not arts:
        # page is not in the standard template: still list it, as one entry
        heading = clean(parser.h1) or clean(parser.title) or path.stem
        arts = [{"id": "a1", "category": "National", "relevance": "", "readMin": 0,
                 "headline": heading, "summary": "", "facts": []}]
        notes.append('no <article class="ca-article"> found, listed as a single entry')
    return arts, notes


def main():
    years = {}
    skipped, warnings, n_days, n_articles = [], [], 0, 0

    for ydir in sorted(p for p in ROOT.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}", p.name)):
        for mdir in sorted(p for p in ydir.iterdir() if p.is_dir()):
            month = MONTH_BY_LOWER.get(mdir.name.lower())
            if not month:
                skipped.append(f"{ydir.name}/{mdir.name}/  (folder name is not an English month)")
                continue
            for f in sorted(mdir.iterdir()):
                if not f.is_file() or f.name.startswith("."):
                    continue
                m = re.fullmatch(r"(\d{1,2})\.html", f.name, re.I)
                if not m or not 1 <= int(m.group(1)) <= 31:
                    skipped.append(f"{ydir.name}/{mdir.name}/{f.name}  (expected a name like 15.html)")
                    continue
                day = str(int(m.group(1)))
                arts, notes = parse_day(f)
                rel = f.relative_to(ROOT).as_posix()
                years.setdefault(ydir.name, {}).setdefault(month, {})[day] = {"path": rel, "articles": arts}
                n_days += 1
                n_articles += len(arts)
                for n in notes:
                    warnings.append(f"{rel}: {n}")

    index = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "years": years}
    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"content-index.json written: {n_days} day page(s), {n_articles} article(s)")
    for y in sorted(years):
        for mo in sorted(years[y], key=MONTHS.index):
            days = sorted(years[y][mo], key=int)
            print(f"  {y}/{mo}: days {', '.join(days)}")
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print("  -", w)
    if skipped:
        print("\nSkipped (not picked up by the viewer):")
        for s in skipped:
            print("  -", s)
    if n_days == 0:
        print("\nNo day pages found yet. Add e.g. 2026/October/15.html and push again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
