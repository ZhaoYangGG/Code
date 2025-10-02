#!/usr/bin/env python3
"""Generate an HTML gallery of books by scraping data from Douban.

This script reads a list of book titles from a text/CSV/JSON file, fetches
metadata from Douban for each title, and produces an HTML file that presents
all of the books in a responsive gallery layout.

Example usage::

    python douban_gallery.py --input books.txt --output gallery.html

The input file can be one of the following formats:

* Plain text (.txt): one title per line.
* CSV (.csv): the first column is treated as the title.
* JSON (.json): either a simple list of titles or a list of objects containing
  a ``title`` key.

The script respects polite scraping practices by throttling requests with a
configurable delay between queries. Douban does not provide an official public
API, so this scraper may break if the website structure changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Iterable, List, Optional

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)

DOUBAN_SEARCH_URL = "https://www.douban.com/search"


@dataclass
class Book:
    """A representation of a book scraped from Douban."""

    query: str
    title: Optional[str] = None
    url: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    rating: Optional[str] = None
    rating_votes: Optional[str] = None
    summary: Optional[str] = None
    cover_url: Optional[str] = None
    info: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_card_html(self) -> str:
        """Render the book as an HTML card for the gallery."""
        title = escape(self.title or self.query)
        summary = escape(self.summary or "暂无简介")
        author_text = ", ".join(self.authors) if self.authors else self.info.get("作者", "未知作者")
        author_text = escape(author_text)
        rating = escape(self.rating) if self.rating else "--"
        votes = escape(self.rating_votes) if self.rating_votes else "0"
        cover_img = (
            f'<img src="{escape(self.cover_url)}" alt="{title}" loading="lazy">'
            if self.cover_url
            else '<div class="placeholder">暂无封面</div>'
        )
        extra_rows = "".join(
            f"<div class='meta-row'><span class='label'>{escape(key)}:</span> {escape(value)}</div>"
            for key, value in self.info.items()
            if key not in {"作者", "译者"}
        )
        error_banner = (
            f"<div class='error'>抓取失败：{escape(self.error)}</div>" if self.error else ""
        )
        url_part = f"<a href='{escape(self.url)}' target='_blank' rel='noopener'>{title}</a>" if self.url else title
        return f"""
        <article class="book-card">
            <div class="cover">{cover_img}</div>
            <div class="content">
                <h2 class="title">{url_part}</h2>
                <div class="authors">{author_text}</div>
                <div class="rating">评分：{rating}（{votes}人评价）</div>
                <p class="summary">{summary}</p>
                {extra_rows}
                {error_banner}
            </div>
        </article>
        """


def read_titles(path: Path) -> List[str]:
    """Read book titles from a supported input file."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".csv":
        titles: List[str] = []
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            for row in reader:
                if not row:
                    continue
                titles.append(row[0].strip())
        return [title for title in titles if title]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        titles: List[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    titles.append(item.strip())
                elif isinstance(item, dict) and "title" in item:
                    titles.append(str(item["title"]).strip())
        return [title for title in titles if title]
    raise ValueError(f"Unsupported file type: {suffix}")


def search_douban(session: requests.Session, title: str) -> Optional[str]:
    """Return the URL of the first Douban search result for a book title."""
    params = {"cat": "1001", "q": title}
    response = session.get(DOUBAN_SEARCH_URL, params=params, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    result = soup.select_one("div.result h3 a")
    if not result or not result.get("href"):
        return None
    return result["href"].split("?")[0]


def _collect_label_value(label: Tag) -> str:
    """Collect the text following a label within the #info block."""
    pieces: List[str] = []
    for sibling in label.next_siblings:
        if isinstance(sibling, NavigableString):
            text = str(sibling).strip()
            if text:
                pieces.append(text)
        elif isinstance(sibling, Tag):
            if sibling.name == "br":
                break
            pieces.append(sibling.get_text(strip=True))
    return " ".join(pieces).strip()


def fetch_book_details(session: requests.Session, url: str, query: str) -> Book:
    """Fetch the details of a book from its Douban page."""
    response = session.get(url, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    book = Book(query=query, url=url)
    title_tag = soup.select_one("span[property='v:itemreviewed']")
    if title_tag:
        book.title = title_tag.get_text(strip=True)

    mainpic = soup.select_one("#mainpic img")
    if mainpic and mainpic.get("src"):
        book.cover_url = mainpic["src"].strip()

    rating_tag = soup.select_one("strong[property='v:average']")
    if rating_tag:
        book.rating = rating_tag.get_text(strip=True)
    votes_tag = soup.select_one("span[property='v:votes']")
    if votes_tag:
        book.rating_votes = votes_tag.get_text(strip=True)

    summary_tag = soup.select_one("#link-report span[property='v:summary']")
    if not summary_tag:
        summary_tag = soup.select_one("#link-report span.all, #link-report div.intro")
    if summary_tag:
        book.summary = " ".join(summary_tag.get_text(" ", strip=True).split())

    info_block = soup.select_one("#info")
    if info_block:
        for label in info_block.select("span.pl"):
            key = label.get_text(strip=True).rstrip(":：")
            value = _collect_label_value(label)
            if value:
                book.info[key] = value
        if "作者" in book.info:
            book.authors = [part.strip() for part in book.info["作者"].split("/") if part.strip()]

    return book


def scrape_books(titles: Iterable[str], delay: float, retries: int) -> List[Book]:
    """Scrape Douban for each title with optional retries and delay."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    titles_list = list(titles)
    total = len(titles_list)

    books: List[Book] = []
    for idx, title in enumerate(titles_list, start=1):
        book = Book(query=title)
        for attempt in range(1, retries + 1):
            try:
                url = search_douban(session, title)
                if not url:
                    raise ValueError("未找到搜索结果")
                book = fetch_book_details(session, url, query=title)
                break
            except Exception as exc:  # noqa: BLE001
                book.error = str(exc)
                if attempt == retries:
                    break
                time.sleep(delay)
        books.append(book)
        if idx < total:
            time.sleep(delay)
    return books


def generate_gallery_html(books: List[Book], title: str) -> str:
    """Generate a gallery-style HTML document for the provided books."""
    cards = "\n".join(book.to_card_html() for book in books)
    return f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>{escape(title)}</title>
    <style>
        :root {{
            color-scheme: light dark;
            font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #f5f5f5;
            color: #222;
        }}
        body {{
            margin: 0;
            padding: 2rem 1rem 4rem;
            background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(240,240,240,0.9));
        }}
        h1 {{
            text-align: center;
            font-weight: 600;
            margin-bottom: 2rem;
        }}
        .gallery {{
            display: grid;
            gap: 1.5rem;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            max-width: 1200px;
            margin: 0 auto;
        }}
        .book-card {{
            background: rgba(255,255,255,0.85);
            border-radius: 16px;
            box-shadow: 0 12px 30px rgba(0,0,0,0.08);
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform .2s ease, box-shadow .2s ease;
        }}
        .book-card:hover {{
            transform: translateY(-6px);
            box-shadow: 0 18px 40px rgba(0,0,0,0.12);
        }}
        .cover {{
            width: 100%;
            padding-top: 150px;
            position: relative;
            background: linear-gradient(135deg, #e2e8f0, #f8fafc);
        }}
        .cover img, .cover .placeholder {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            max-width: 70%;
            max-height: 90%;
            border-radius: 8px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.1);
        }}
        .cover .placeholder {{
            padding: 1rem;
            background: rgba(255,255,255,0.7);
            text-align: center;
            font-size: 0.9rem;
            color: #555;
        }}
        .content {{
            padding: 1.25rem 1.5rem 1.75rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            flex: 1;
        }}
        .title {{
            font-size: 1.2rem;
            margin: 0;
        }}
        .title a {{
            color: inherit;
            text-decoration: none;
            border-bottom: 1px solid transparent;
        }}
        .title a:hover {{
            border-bottom-color: currentColor;
        }}
        .authors, .rating, .meta-row {{
            font-size: 0.95rem;
            color: #555;
        }}
        .summary {{
            font-size: 0.95rem;
            line-height: 1.6;
            color: #333;
            max-height: 8rem;
            overflow: hidden;
            position: relative;
        }}
        .summary::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 2rem;
            background: linear-gradient(180deg, rgba(255,255,255,0), rgba(255,255,255,0.95));
        }}
        .label {{
            font-weight: 600;
            margin-right: 0.4rem;
        }}
        .error {{
            margin-top: auto;
            background: rgba(255,59,48,0.15);
            color: #b00020;
            padding: 0.6rem 0.8rem;
            border-radius: 12px;
            font-size: 0.9rem;
        }}
        footer {{
            text-align: center;
            margin-top: 3rem;
            color: #777;
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <h1>{escape(title)}</h1>
    <section class="gallery">
        {cards}
    </section>
    <footer>数据来源：豆瓣读书（可能存在版权限制，仅供学习交流）</footer>
</body>
</html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="从豆瓣抓取书籍信息并生成画廊视图")
    parser.add_argument("--input", required=True, type=Path, help="包含书名的输入文件（txt/csv/json）")
    parser.add_argument("--output", type=Path, default=Path("book_gallery.html"), help="输出 HTML 文件路径")
    parser.add_argument("--title", default="豆瓣书籍画廊", help="画廊页面标题")
    parser.add_argument("--delay", type=float, default=2.0, help="每次请求之间的延迟（秒）")
    parser.add_argument("--retries", type=int, default=2, help="每本书的重试次数")
    args = parser.parse_args(argv)

    try:
        titles = read_titles(args.input)
    except Exception as exc:  # noqa: BLE001
        print(f"读取输入文件失败: {exc}", file=sys.stderr)
        return 1

    if not titles:
        print("输入文件中未找到书名", file=sys.stderr)
        return 1

    print(f"共加载 {len(titles)} 本书，将从豆瓣抓取数据……")

    books = scrape_books(titles, delay=args.delay, retries=args.retries)

    html = generate_gallery_html(books, title=args.title)
    args.output.write_text(html, encoding="utf-8")
    print(f"画廊已生成：{args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
