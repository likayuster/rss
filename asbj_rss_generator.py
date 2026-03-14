import argparse
import email.utils
import re
import sys
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.asb-j.jp"
HTTP_BASE_URL = "http://www.asb-j.jp"
LIST_URL = f"{BASE_URL}/jp/information.html"


def to_http(url: str) -> str:
    p = urlparse(url)
    return urlunparse(("http", p.netloc, p.path, p.params, p.query, p.fragment))


def fetch(url: str, timeout: int = 30) -> requests.Response:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r
    except requests.exceptions.SSLError:
        http_url = to_http(url)
        r = requests.get(http_url, timeout=timeout)
        r.raise_for_status()
        return r


def normalize_article_url(href: str) -> str:
    url = urljoin(BASE_URL, href)
    return to_http(url)


def parse_list_page():
    resp = fetch(LIST_URL)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for a in soup.select("a[href]"):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href", "")
        if not href:
            continue
        if not re.search(r"\d{4}年\d{2}月\d{2}日", text):
            continue
        url = normalize_article_url(href)
        items.append((text, url))

    seen = set()
    uniq = []
    for text, url in items:
        if url in seen:
            continue
        seen.add(url)
        uniq.append((text, url))
    return uniq


def parse_article(url: str):
    resp = fetch(url)
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    title = None
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)

    text = soup.get_text("\n", strip=True)
    m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", text)
    pub_date = None
    if m:
        y, mo, d = map(int, m.groups())
        pub_date = datetime(y, mo, d, 0, 0, 0)

    desc = " ".join(text.split())
    if len(desc) > 300:
        desc = desc[:300] + "..."

    return {
        "title": title or url,
        "link": url,
        "description": desc,
        "pub_date": pub_date,
    }


def build_rss(items, feed_url: str, site_url: str):
    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<rss version="2.0">')
    out.append("<channel>")
    out.append("<title>ASBJ Information</title>")
    out.append(f"<link>{site_url}</link>")
    out.append("<description>ASBJ Information latest articles</description>")
    out.append("<language>ja</language>")
    out.append(f"<atom:link href=\"{feed_url}\" rel=\"self\" type=\"application/rss+xml\" xmlns:atom=\"http://www.w3.org/2005/Atom\" />")

    for item in items:
        out.append("<item>")
        out.append(f"<title><![CDATA[{item['title']}]]></title>")
        out.append(f"<link>{item['link']}</link>")
        out.append(f"<guid>{item['link']}</guid>")
        out.append(f"<description><![CDATA[{item['description']}]]></description>")
        if item["pub_date"]:
            out.append(f"<pubDate>{email.utils.format_datetime(item['pub_date'])}</pubDate>")
        out.append("</item>")

    out.append("</channel>")
    out.append("</rss>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--feed-url", required=True)
    ap.add_argument("--site-url", default="http://www.asb-j.jp/jp/information.html")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    rows = parse_list_page()[: args.limit]
    items = [parse_article(url) for _, url in rows]
    rss = build_rss(items, args.feed_url, args.site_url)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(rss)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
