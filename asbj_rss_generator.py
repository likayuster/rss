#!/usr/bin/env python3
"""Generate an RSS 2.0 feed for ASBJ Information.

This script scrapes the ASBJ Information page and writes an RSS file.
It is designed to be tolerant of small HTML changes by relying on link
patterns and text heuristics rather than brittle CSS selectors.

Usage:
    python asbj_rss_generator.py --output asbj-information.xml --limit 20

Requires:
    pip install requests beautifulsoup4
"""
from __future__ import annotations

import argparse
import email.utils
import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, tostring

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.asb-j.jp"
LIST_URL = f"{BASE_URL}/jp/information.html"
TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (compatible; ASBJ-RSS-Generator/1.0; +https://example.invalid/)"

# Example item text on the list page:
# 2026年02月20日 国際関連情報 IASBが、IAS第28号における公正価値オプションの明確化について公開協議
LIST_ITEM_RE = re.compile(
    r"^\s*(?P<year>\d{4})年\s*(?P<month>\d{2})月\s*(?P<day>\d{2})日\s+"
    r"(?P<category>\S+)\s+(?P<title>.+?)\s*$"
)
DATE_RE = re.compile(r"(?P<year>\d{4})年\s*(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})日")


@dataclass
class Entry:
    title: str
    link: str
    category: str
    published: datetime
    description: str
    guid: str


class FetchError(RuntimeError):
    pass


session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def fetch_html(url: str) -> str:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding or "utf-8"
    return response.text


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_japanese_date(text: str) -> datetime | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    return datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)


def discover_entries(list_url: str, limit: int) -> List[Entry]:
    html_text = fetch_html(list_url)
    soup = BeautifulSoup(html_text, "html.parser")
    seen: set[str] = set()
    entries: list[Entry] = []

    for a in soup.find_all("a", href=True):
        text = normalize_whitespace(a.get_text(" ", strip=True))
        match = LIST_ITEM_RE.match(text)
        if not match:
            continue

        href = urljoin(list_url, a["href"])
        if href in seen:
            continue
        seen.add(href)

        published = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            tzinfo=timezone.utc,
        )
        category = match.group("category")
        title = match.group("title")
        description = extract_description(href)

        entries.append(
            Entry(
                title=title,
                link=href,
                category=category,
                published=published,
                description=description,
                guid=href,
            )
        )
        if len(entries) >= limit:
            break

    if not entries:
        raise FetchError(
            "No entries were found. The page structure may have changed and the parser needs updating."
        )

    return entries


BOILERPLATE_PATTERNS = [
    re.compile(r"^To Top$"),
    re.compile(r"^以 上$"),
    re.compile(r"^\*+\s*$"),
    re.compile(r"^公開草案を読んでコメントを$"),
]


def is_boilerplate(text: str) -> bool:
    text = normalize_whitespace(text)
    if not text:
        return True
    return any(p.search(text) for p in BOILERPLATE_PATTERNS)


# Try a few common content containers, then fall back to paragraphs in the page body.
CONTENT_SELECTORS = [
    "main",
    "article",
    ".article",
    ".entry-content",
    ".post-content",
    ".contents",
    "#main",
    "#content",
]


def extract_description(article_url: str, max_chars: int = 280) -> str:
    try:
        html_text = fetch_html(article_url)
    except Exception:
        return ""

    soup = BeautifulSoup(html_text, "html.parser")

    container = None
    for selector in CONTENT_SELECTORS:
        container = soup.select_one(selector)
        if container:
            break
    if container is None:
        container = soup.body or soup

    paragraphs: list[str] = []
    for elem in container.find_all(["p", "li"]):
        text = normalize_whitespace(elem.get_text(" ", strip=True))
        if is_boilerplate(text):
            continue
        # Avoid using a naked date line as the description.
        if DATE_RE.fullmatch(text):
            continue
        paragraphs.append(text)
        if len(" ".join(paragraphs)) >= max_chars:
            break

    description = normalize_whitespace(" ".join(paragraphs))
    if len(description) > max_chars:
        description = description[: max_chars - 1].rstrip() + "…"
    return description


def rfc2822(dt: datetime) -> str:
    return email.utils.format_datetime(dt)


def build_rss(entries: Iterable[Entry], feed_url: str | None = None) -> bytes:
    entries = list(entries)
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = "ASBJ Information"
    SubElement(channel, "link").text = LIST_URL
    SubElement(channel, "description").text = "Latest entries from ASBJ Information"
    SubElement(channel, "language").text = "ja"
    if feed_url:
        atom_ns = "http://www.w3.org/2005/Atom"
        rss.set("xmlns:atom", atom_ns)
        SubElement(channel, f"{{{atom_ns}}}link", href=feed_url, rel="self", type="application/rss+xml")

    if entries:
        latest = max(entry.published for entry in entries)
        SubElement(channel, "lastBuildDate").text = rfc2822(latest)

    for entry in entries:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = entry.title
        SubElement(item, "link").text = entry.link
        SubElement(item, "guid", isPermaLink="true").text = entry.guid
        SubElement(item, "pubDate").text = rfc2822(entry.published)
        SubElement(item, "category").text = entry.category
        if entry.description:
            SubElement(item, "description").text = html.escape(entry.description)

    xml_bytes = tostring(rss, encoding="utf-8", xml_declaration=True)
    return xml_bytes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an RSS feed for ASBJ Information")
    parser.add_argument("--output", default="asbj-information.xml", help="Output RSS XML path")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of items to include")
    parser.add_argument("--feed-url", default="", help="Public URL where the RSS will be hosted")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        entries = discover_entries(LIST_URL, limit=args.limit)
        rss_xml = build_rss(entries, feed_url=args.feed_url or None)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    with open(args.output, "wb") as f:
        f.write(rss_xml)

    print(f"Wrote {len(entries)} items to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
