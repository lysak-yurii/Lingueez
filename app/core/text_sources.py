"""Fetch reading texts from internet sources (Wikipedia, web pages, RSS).

Pure, GUI-free helpers. Every fetch failure is raised as :class:`SourceError`
with a message fit for showing to the user directly (mirroring ai.AIError).
All functions do blocking network I/O — callers run them in worker threads.
"""
import html
import json
import logging
import re
from urllib.parse import urlparse

import requests

from app.config import load_settings, save_settings
from app.core.audio import lang_codes

TIMEOUT = 15
HEADERS = {"User-Agent": "DictionaryApp/1.0 (language-learning desktop app)"}

_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^=+\s*(.*?)\s*=+\s*$", re.M)  # == wiki headings ==


class SourceError(Exception):
    """A fetch failure with a user-presentable message."""


def _get(url, params=None):
    host = urlparse(url).netloc or url
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.Timeout:
        raise SourceError(f"{host} took too long to respond. Try again later.")
    except requests.RequestException:
        raise SourceError(f"Could not reach {host}. "
                          f"Check your internet connection.")
    if resp.status_code >= 400:
        raise SourceError(f"{host} returned an error (HTTP {resp.status_code}). "
                          f"Try again later.")
    return resp


def _strip_html(markup):
    return html.unescape(_TAG_RE.sub("", markup or "")).strip()


def _trim(text, max_chars):
    """Cut overlong text at a sentence boundary."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > max_chars // 2 else cut).strip()


# ---------------------------------------------------------------- Wikipedia

_WIKI_LANG_OVERRIDES = {"zh-CN": "zh", "zh-HK": "zh", "fil": "tl", "iw": "he"}


def wiki_lang(language_name):
    """Wikipedia subdomain for a language name from audio.lang_codes."""
    code = lang_codes.get(language_name, "")
    if not code:
        return "en"
    code = _WIKI_LANG_OVERRIDES.get(code, code)
    return code.split("-")[0].lower() or "en"


def wikipedia_search(query, language, limit=8):
    """Search articles; returns [{title, description, excerpt}]."""
    lang = wiki_lang(language)
    resp = _get(f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page",
                params={"q": query, "limit": limit})
    pages = resp.json().get("pages") or []
    if not pages:
        raise SourceError(f"No Wikipedia articles found for '{query}'.")
    return [{
        "title": p.get("title") or "",
        "description": p.get("description") or "",
        "excerpt": _strip_html(p.get("excerpt")),
    } for p in pages]


def wikipedia_fetch(title, language, max_chars=8000):
    """Full plain-text article; returns (title, text)."""
    lang = wiki_lang(language)
    resp = _get(f"https://{lang}.wikipedia.org/w/api.php", params={
        "action": "query", "prop": "extracts", "explaintext": 1,
        "redirects": 1, "format": "json", "formatversion": 2, "titles": title,
    })
    pages = resp.json().get("query", {}).get("pages") or []
    extract = (pages[0].get("extract") or "").strip() if pages else ""
    if not extract:
        raise SourceError(f"Could not load the article '{title}'.")
    extract = _HEADING_RE.sub(r"\1", extract)
    return pages[0].get("title") or title, _trim(extract, max_chars)


# --------------------------------------------------------------- URL / RSS

def extract_url(url):
    """Readable article text from any web page; returns (title, text)."""
    url = url.strip()
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    resp = _get(url)
    try:
        import trafilatura
    except ImportError:
        raise SourceError("URL extraction needs the 'trafilatura' package. "
                          "Run: pip install trafilatura")
    try:
        text = trafilatura.extract(resp.text, url=url, include_comments=False)
    except Exception as exc:
        logging.warning(f"trafilatura failed on {url}: {exc}")
        text = None
    if not text or not text.strip():
        raise SourceError("Could not extract readable text from this page. "
                          "It may need a login or be script-only.")
    title = ""
    try:
        meta = trafilatura.extract_metadata(resp.text)
        title = (getattr(meta, "title", "") or "") if meta else ""
    except Exception:
        pass
    return title.strip(), text.strip()


def fetch_feed(feed_url):
    """Recent feed entries; returns [{title, link, published, summary}]."""
    try:
        import feedparser
    except ImportError:
        raise SourceError("RSS needs the 'feedparser' package. "
                          "Run: pip install feedparser")
    resp = _get(feed_url)  # feedparser itself has no timeout
    parsed = feedparser.parse(resp.content)
    entries = [{
        "title": (e.get("title") or "").strip(),
        "link": e.get("link") or "",
        "published": (e.get("published") or e.get("updated") or "")[:25],
        "summary": e.get("summary") or e.get("description") or "",
    } for e in parsed.entries[:30]]
    if not entries:
        raise SourceError("This feed has no entries "
                          "(or is not a valid RSS/Atom feed).")
    return entries


def fetch_feed_entry(entry):
    """Full text for one feed entry; falls back to the entry's own summary."""
    title = (entry.get("title") or "").strip()
    summary = _strip_html(entry.get("summary"))
    link = entry.get("link") or ""
    if link:
        try:
            page_title, text = extract_url(link)
            # JS-only pages extract to a stub of navigation text — prefer the
            # feed's own summary when it clearly carries more content
            if len(text) >= 500 or len(text) > len(summary):
                return (page_title or title), text
        except SourceError as exc:
            logging.info(f"Feed entry extraction failed for {link}: {exc}")
    if not summary:
        raise SourceError("Could not load this entry's article or summary.")
    return title, summary


# ------------------------------------------------------------------- feeds

CURATED_FEEDS = {
    "German": [
        {"name": "DW – Langsam gesprochene Nachrichten",
         "url": "https://rss.dw.com/xml/DKpodcast_lgn_de"},
        {"name": "DW – Nachrichten",
         "url": "https://rss.dw.com/rdf/rss-de-top"},
        {"name": "Tagesschau",
         "url": "https://www.tagesschau.de/index~rss2.xml"},
    ],
    "French": [
        {"name": "RFI – Journal en français facile",
         "url": "https://www.rfi.fr/fr/podcasts/journal-fran%C3%A7ais-facile/podcast"},
        {"name": "Le Monde – À la une",
         "url": "https://www.lemonde.fr/rss/une.xml"},
    ],
    "English": [
        {"name": "VOA Learning English",
         "url": "https://learningenglish.voanews.com/podcast/?zoneId=1689"},
        {"name": "BBC News – Top stories",
         "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    ],
    "Greek": [
        {"name": "ERT News",
         "url": "https://www.ertnews.gr/feed/"},
        {"name": "To Vima",
         "url": "https://www.tovima.gr/feed/"},
        {"name": "Naftemporiki",
         "url": "https://www.naftemporiki.gr/feed/"},
    ],
    "Spanish": [
        {"name": "BBC Mundo",
         "url": "https://www.bbc.co.uk/mundo/index.xml"},
        {"name": "20minutos – Portada",
         "url": "https://www.20minutos.es/rss/"},
    ],
}


def user_feeds(settings=None):
    """User-defined feeds from settings: [{name, url, language}]."""
    settings = settings or load_settings()
    try:
        feeds = json.loads(settings.get("rss_feeds_user", "[]") or "[]")
    except (TypeError, ValueError):
        logging.warning("Could not parse rss_feeds_user setting; ignoring it.")
        return []
    return [f for f in feeds if isinstance(f, dict) and f.get("url")]


def save_user_feeds(feeds):
    settings = load_settings()
    settings["rss_feeds_user"] = json.dumps(list(feeds), ensure_ascii=False)
    save_settings(settings)


def feeds_for_language(language):
    """Curated + user feeds for a language; user feeds tagged with 'user'."""
    feeds = [dict(f) for f in CURATED_FEEDS.get(language, [])]
    for feed in user_feeds():
        if not feed.get("language") or feed.get("language") == language:
            feeds.append(dict(feed, user=True))
    return feeds
