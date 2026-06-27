# -*- coding: utf-8 -*-
"""
arxiv2discord
=============
Fetch new arXiv astro-ph papers via the official arXiv API, score them by
keyword, and post matches to a Discord channel through a webhook.

Based in spirit on Y-Masayuki/arXiv-owl (fork of jinshisai/arXiv-owl, in turn
based on fkubota/Carrier-Owl), but rewritten to use the official arXiv API
instead of HTML scraping and to deliver to Discord instead of Slack.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Iterable

import feedparser
import requests
import yaml

ARXIV_API = "http://export.arxiv.org/api/query"
# arXiv rejects the default python-requests User-Agent, so identify ourselves.
USER_AGENT = "arxiv2discord/1.0 (https://github.com/Y-Masayuki/astroph2discord)"
ARXIV_PAGE_SIZE = 100          # results per API request
ARXIV_RATE_LIMIT_SEC = 3.0     # arXiv asks for >=3 s between requests
DISCORD_MAX_EMBEDS = 10        # Discord allows at most 10 embeds per message
DISCORD_DESC_LIMIT = 2000      # keep abstracts well under the 4096 embed limit
# Discord rejects a message whose embeds sum to >6000 characters. Pack messages
# under a safe budget below that hard limit.
DISCORD_TOTAL_CHAR_BUDGET = 5800
DISCORD_TITLE_LIMIT = 256

# Default: all six astro-ph subcategories (modern papers carry these, not the
# legacy bare "astro-ph" tag).
DEFAULT_CATEGORIES = [
    "astro-ph.GA",
    "astro-ph.CO",
    "astro-ph.EP",
    "astro-ph.HE",
    "astro-ph.IM",
    "astro-ph.SR",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    arxiv_id: str
    url: str
    pdf_url: str
    title: str
    authors: list
    abstract: str
    primary_category: str
    categories: list
    published: dt.datetime
    updated: dt.datetime
    comment: str = ""
    journal_ref: str = ""
    title_ja: str = ""
    abstract_ja: str = ""
    score: float = 0.0
    hit_keywords: list = field(default_factory=list)
    hit_authors: list = field(default_factory=list)

    def author_str(self, limit: int = 8, highlight: set | None = None) -> str:
        highlight = highlight or set()

        def render(name: str) -> str:
            if any(h in name.lower() for h in highlight):
                return f"**{name}**"
            return name

        names = [render(a) for a in self.authors]
        if len(names) <= limit:
            return ", ".join(names)
        return ", ".join(names[:limit]) + f", … (+{len(self.authors) - limit})"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config(path: str | None = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    config.setdefault("categories", DEFAULT_CATEGORIES)
    config.setdefault("keywords", {})
    config.setdefault("score_threshold", 0)
    config.setdefault("days", 3)
    config.setdefault("max_results", 400)
    # 'updated' catches replacements / late updates within the window;
    # 'submitted' uses only the original v1 date.
    config.setdefault("date_field", "updated")

    # Translation (DeepL). Requires the DEEPL_API_KEY environment variable.
    config.setdefault("translate", False)
    config.setdefault("translate_lang", "JA")

    # Author highlighting. Papers by these authors get a ⭐ and bold names;
    # `author_bonus` is added to their score (set >= score_threshold to make
    # sure your own papers always notify, even with no keyword hit).
    config.setdefault("highlight_authors", [])
    config.setdefault("author_bonus", 0)

    # normalise types
    config["keywords"] = {str(k): float(v) for k, v in config["keywords"].items()}
    config["score_threshold"] = float(config["score_threshold"])
    config["author_bonus"] = float(config["author_bonus"])
    config["highlight_authors"] = [str(a).lower() for a in config["highlight_authors"]]
    return config


# --------------------------------------------------------------------------- #
# arXiv API
# --------------------------------------------------------------------------- #
def _entry_to_result(entry) -> Result:
    arxiv_id = entry.id.split("/abs/")[-1]
    published = dt.datetime(*entry.published_parsed[:6], tzinfo=dt.timezone.utc)
    updated_parsed = getattr(entry, "updated_parsed", None) or entry.published_parsed
    updated = dt.datetime(*updated_parsed[:6], tzinfo=dt.timezone.utc)
    authors = [a.get("name", "") for a in getattr(entry, "authors", [])]

    pdf_url = ""
    for link in getattr(entry, "links", []):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href", "")
            break

    categories = [t.get("term", "") for t in getattr(entry, "tags", [])]
    primary = ""
    if hasattr(entry, "arxiv_primary_category"):
        primary = entry.arxiv_primary_category.get("term", "")

    comment = " ".join(getattr(entry, "arxiv_comment", "").split())
    journal_ref = " ".join(getattr(entry, "arxiv_journal_ref", "").split())

    return Result(
        arxiv_id=arxiv_id,
        url=entry.id,
        pdf_url=pdf_url,
        title=" ".join(entry.title.split()),
        authors=authors,
        abstract=" ".join(entry.summary.split()),
        primary_category=primary,
        categories=categories,
        published=published,
        updated=updated,
        comment=comment,
        journal_ref=journal_ref,
    )


def fetch_articles(categories: Iterable[str], days: int,
                   max_results: int = 400,
                   date_field: str = "updated") -> list[Result]:
    """Fetch recent papers, newest first, stopping once we pass the date cutoff.

    date_field: 'updated' sorts/filters by last-updated date (catches v2
    replacements and updates within the window); 'submitted' uses the original
    submission date only.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    search_query = " OR ".join(f"cat:{c}" for c in categories)
    use_updated = date_field == "updated"
    sort_key = "lastUpdatedDate" if use_updated else "submittedDate"

    results: list[Result] = []
    start = 0
    while start < max_results:
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": min(ARXIV_PAGE_SIZE, max_results - start),
            "sortBy": sort_key,
            "sortOrder": "descending",
        }
        url = ARXIV_API + "?" + urllib.parse.urlencode(params)

        feed = _request_feed(url)
        if not feed.entries:
            break

        reached_cutoff = False
        for entry in feed.entries:
            result = _entry_to_result(entry)
            ref_date = result.updated if use_updated else result.published
            if ref_date < cutoff:
                reached_cutoff = True
                break
            results.append(result)

        if reached_cutoff or len(feed.entries) < params["max_results"]:
            break

        start += ARXIV_PAGE_SIZE
        time.sleep(ARXIV_RATE_LIMIT_SEC)

    return results


def _request_feed(url: str, retries: int = 3):
    """Fetch and parse one page of the arXiv API, with simple retries."""
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30,
                                 headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if feed.entries or not feed.bozo:
                return feed
            last_err = getattr(feed, "bozo_exception", "empty feed")
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(ARXIV_RATE_LIMIT_SEC * (attempt + 1))
    print(f"WARNING: arXiv API request failed after {retries} tries: {last_err}",
          file=sys.stderr)
    return feedparser.parse("")  # empty feed


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def score_article(result: Result, keywords: dict) -> tuple[float, list]:
    text = (result.title + " " + result.abstract).lower()
    total = 0.0
    hits = []
    for word, weight in keywords.items():
        if word.lower() in text:
            total += weight
            hits.append(word)
    return total, hits


def matched_authors(result: Result, highlight_authors: list) -> list:
    """Return the display names of authors matching the highlight list."""
    hits = []
    for name in result.authors:
        low = name.lower()
        if any(h in low for h in highlight_authors):
            hits.append(name)
    return hits


def filter_and_score(results: list[Result], keywords: dict, threshold: float,
                     highlight_authors: list | None = None,
                     author_bonus: float = 0.0) -> list[Result]:
    highlight_authors = highlight_authors or []
    scored = []
    for result in results:
        score, hits = score_article(result, keywords)
        author_hits = matched_authors(result, highlight_authors)
        if author_hits:
            score += author_bonus
        if score > 0 and score >= threshold:
            result.score = score
            result.hit_keywords = hits
            result.hit_authors = author_hits
            scored.append(result)
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored


# --------------------------------------------------------------------------- #
# Translation (DeepL)
# --------------------------------------------------------------------------- #
DEEPL_BATCH = 40  # DeepL accepts up to 50 texts per request


def translate_texts(texts: list[str], api_key: str,
                    target_lang: str = "JA") -> list[str]:
    """Translate a list of texts via the DeepL API, preserving order."""
    if not texts:
        return []
    # Free keys end with ":fx" and use the api-free host.
    host = "api-free.deepl.com" if api_key.rstrip().endswith(":fx") else "api.deepl.com"
    endpoint = f"https://{host}/v2/translate"
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}

    out: list[str] = []
    for i in range(0, len(texts), DEEPL_BATCH):
        chunk = texts[i: i + DEEPL_BATCH]
        data = [("target_lang", target_lang)] + [("text", t) for t in chunk]
        resp = requests.post(endpoint, headers=headers, data=data, timeout=60)
        resp.raise_for_status()
        out.extend(tr["text"] for tr in resp.json()["translations"])
        time.sleep(0.5)
    return out


def translate_results(results: list[Result], api_key: str,
                      target_lang: str = "JA") -> None:
    """Fill in title_ja / abstract_ja for each result, in place.

    On any DeepL error we log and continue with English only — translation is a
    nice-to-have and must never block notifications.
    """
    if not results:
        return
    payload = []
    for r in results:
        payload.append(r.title)
        payload.append(r.abstract)
    try:
        translated = translate_texts(payload, api_key, target_lang)
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"WARNING: DeepL translation failed, sending English only: {exc}",
              file=sys.stderr)
        return
    if len(translated) != len(payload):
        print("WARNING: DeepL returned an unexpected count; skipping translation.",
              file=sys.stderr)
        return
    for idx, r in enumerate(results):
        r.title_ja = translated[2 * idx]
        r.abstract_ja = translated[2 * idx + 1]


# --------------------------------------------------------------------------- #
# Discord delivery
# --------------------------------------------------------------------------- #
def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _score_color(score: float) -> int:
    if score >= 3:
        return 0xE74C3C  # red
    if score >= 2:
        return 0xE67E22  # orange
    return 0x3498DB      # blue


def build_embed(result: Result, highlight: set | None = None) -> dict:
    highlight = highlight or set()
    revised = result.updated.date() != result.published.date()

    # Title: flag revisions (🔄) and authored-by-watchlist papers (⭐).
    title = result.title
    flags = ""
    if result.hit_authors:
        flags += "⭐"
    if revised:
        flags += "🔄"
    if flags:
        title = f"{flags} {title}"

    # When a Japanese translation exists, each abstract gets a smaller budget so
    # both fit; otherwise the English abstract gets the full budget.
    translated = bool(result.abstract_ja)
    abs_budget = 700 if translated else DISCORD_DESC_LIMIT - 300

    lines = []
    if result.title_ja:
        lines.append(f"**🇯🇵 {result.title_ja}**")
    hit_str = ", ".join(result.hit_keywords) or "—"
    lines.append(f"**Score** `{result.score:g}`  |  **Hits** {hit_str}")
    lines.append(f"**Authors** {result.author_str(highlight=highlight)}")
    lines.append(f"**Categories** {', '.join(result.categories)}")
    if result.comment:
        lines.append(f"**Comments** {_truncate(result.comment, 300)}")
    if result.journal_ref:
        lines.append(f"**Journal** {_truncate(result.journal_ref, 200)}")
    lines.append(f"**PDF** {result.pdf_url}")
    lines.append("")
    lines.append(_truncate(result.abstract, abs_budget))
    if translated:
        lines.append("")
        lines.append("**【和訳】** " + _truncate(result.abstract_ja, abs_budget))

    footer = f"arXiv:{result.arxiv_id}  ·  submitted {result.published:%Y-%m-%d}"
    if revised:
        footer += f"  ·  revised {result.updated:%Y-%m-%d}"
    return {
        "title": _truncate(title, DISCORD_TITLE_LIMIT),
        "url": result.url,
        "description": _truncate("\n".join(lines), 4096),
        "color": _score_color(result.score),
        "footer": {"text": footer},
    }


def post_to_discord(webhook_url: str, results: list[Result],
                    config: dict, dry_run: bool = False) -> None:
    today = dt.date.today()
    cats = ", ".join(config["categories"])
    header = (f"📡 **arXiv astro-ph digest — {today:%Y-%m-%d}**\n"
              f"{len(results)} matching paper(s)  ·  categories: {cats}")

    _send(webhook_url, {"content": header}, dry_run)

    highlight = set(config.get("highlight_authors", []))

    # Discord limits a message to <=10 embeds AND <=6000 chars across all of
    # them combined. Pack greedily, respecting both constraints.
    for batch in _pack_embeds(build_embed(r, highlight) for r in results):
        _send(webhook_url, {"embeds": batch}, dry_run)


def _embed_len(embed: dict) -> int:
    """Character count Discord uses toward the 6000-per-message limit."""
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    total += len(embed.get("author", {}).get("name", ""))
    for f in embed.get("fields", []):
        total += len(f.get("name", "")) + len(f.get("value", ""))
    return total


def _pack_embeds(embeds: Iterable[dict]) -> list[list[dict]]:
    """Group embeds into messages of <=10 embeds and <=budget total chars."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    running = 0
    for embed in embeds:
        size = _embed_len(embed)
        if current and (len(current) >= DISCORD_MAX_EMBEDS
                        or running + size > DISCORD_TOTAL_CHAR_BUDGET):
            batches.append(current)
            current, running = [], 0
        current.append(embed)
        running += size
    if current:
        batches.append(current)
    return batches


def _send(webhook_url: str, payload: dict, dry_run: bool) -> None:
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
        return
    resp = requests.post(webhook_url, json=payload, timeout=30)
    # Basic handling of Discord rate limiting.
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 1)
        time.sleep(float(retry_after) + 0.5)
        resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)  # be gentle with the webhook


# --------------------------------------------------------------------------- #
# Seen-ID cache (avoid duplicates; provide catch-up safety across runs)
# --------------------------------------------------------------------------- #
SEEN_RETENTION_DAYS = 120


def load_seen(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        print(f"WARNING: could not read state file {path}; starting fresh.",
              file=sys.stderr)
        return {}


def save_seen(path: str, seen: dict) -> None:
    # Prune entries older than the retention window to keep the file small.
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(days=SEEN_RETENTION_DAYS)).date().isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(pruned, fh, indent=0, sort_keys=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(config_path: str | None, webhook_url: str | None,
        days: int | None, dry_run: bool,
        state_file: str | None) -> int:
    config = load_config(config_path)
    if days is not None:
        config["days"] = days

    if not config["keywords"]:
        print("ERROR: no keywords configured in config.yaml", file=sys.stderr)
        return 1

    articles = fetch_articles(config["categories"], config["days"],
                              config["max_results"], config["date_field"])
    matches = filter_and_score(articles, config["keywords"],
                               config["score_threshold"],
                               config["highlight_authors"],
                               config["author_bonus"])

    # De-duplicate against papers already notified in previous runs. The key is
    # the versioned arXiv id (e.g. 2605.11486v2), so a v2 replacement is treated
    # as new and re-notified.
    seen = load_seen(state_file) if state_file else {}
    fresh = [m for m in matches if m.arxiv_id not in seen]

    print(f"Fetched {len(articles)} papers from the last {config['days']} day(s) "
          f"(by {config['date_field']} date); {len(matches)} matched, "
          f"{len(fresh)} new after de-duplication.", file=sys.stderr)

    if not fresh:
        print("Nothing new to send.", file=sys.stderr)
        return 0

    if not dry_run and not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    # Optional Japanese translation (DeepL). Never blocks notification.
    if config["translate"]:
        deepl_key = os.getenv("DEEPL_API_KEY")
        if deepl_key:
            translate_results(fresh, deepl_key, config["translate_lang"])
        else:
            print("WARNING: translate is on but DEEPL_API_KEY is not set; "
                  "sending English only.", file=sys.stderr)

    post_to_discord(webhook_url, fresh, config, dry_run=dry_run)

    # Record what we sent so we never repeat it. Skip during dry runs.
    if state_file and not dry_run:
        today = dt.date.today().isoformat()
        for m in fresh:
            seen[m.arxiv_id] = today
        save_seen(state_file, seen)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Post new arXiv papers to Discord.")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--days", type=int, default=None,
                        help="look back this many days (overrides config)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the Discord payload instead of sending it")
    parser.add_argument("--state-file", default="seen_ids.json",
                        help="JSON file of already-notified arXiv ids "
                             "(de-duplication); default: seen_ids.json")
    parser.add_argument("--no-state", action="store_true",
                        help="disable the seen-id cache (notify every match)")
    args = parser.parse_args()

    state_file = None if args.no_state else args.state_file
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    sys.exit(run(args.config, webhook_url, args.days, args.dry_run, state_file))


if __name__ == "__main__":
    main()
