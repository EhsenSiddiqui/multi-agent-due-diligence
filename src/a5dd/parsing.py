"""
Turns raw, unstructured web content (HTML pages, PDF files) into a clean SourceDocument.

This is the "scraping" layer. Any search provider (the offline simulated web, or a live
API later) hands raw bytes + a URL to `build_document`, and gets back the same
SourceDocument contract that stage 3 expects.

WHAT IT DOES
  HTML: removes scripts, menus, cookie banners, ads, newsletter boxes, "related stories",
        comments and share bars; keeps the article body; collects in-article links.
  PDF:  extracts text with pypdf; drops page numbers and repeated headers/footers;
        re-joins lines that were wrapped mid-sentence.
  Both: finds the title, the publication date and a publisher hint, and records HOW each
        was found in `parse_notes` (so a reviewer can check doubtful dates).
  Source type: from the organisation's own websites (case card), the curated domain list
        (config/sources.json), or fallback rules. Unknown sources get low reliability.

SIMPLIFIED
  - Boilerplate removal uses tag names and class/id keywords. Real sites vary a lot;
    a library such as trafilatura does this better across thousands of layouts.
  - Dates are taken from metadata or the first date-like text; a PDF's own metadata
    date is ignored because it is usually the file's creation date, not the document date.
  - No OCR: scanned PDFs (images of text) come back empty and are reported as a gap.

BETTER
  trafilatura (or readability-lxml) for article extraction, OCR (e.g. Tesseract) for
  scanned PDFs, language detection + translation for non-English sources.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .schemas import CaseCard, Link, Origin, SourceDocument

# Class / id fragments that mark page furniture rather than article content.
BOILERPLATE_HINTS = ("cookie", "consent", "ad-slot", "advert", "newsletter", "related", "share", "comment",
                     "donate", "promo", "subscribe", "fiction-note", "breadcrumb", "sidebar", "social")
BOILERPLATE_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "button",
                    "iframe", "svg")

MONTHS = ("january february march april may june july august september october november december").split()
_MONTH_RE = "|".join(m.capitalize() for m in MONTHS)
DATE_PATTERNS = [
    (re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_RE})\s+(\d{{4}})\b"), "dmy"),     # 12 August 2025
    (re.compile(rf"\b({_MONTH_RE})\s+(\d{{1,2}}),?\s+(\d{{4}})\b"), "mdy"),   # March 14, 2019
    (re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"), "iso"),             # 2019-03-14 or 2019-03-14T07:30
]


@dataclass
class Parsed:
    """Intermediate result of parsing one file."""
    title: str
    text: str
    published_date: date | None
    publisher_hint: str
    links: list[Link] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------- dates

def find_date(text: str) -> date | None:
    """First date-like expression in the text, or None."""
    best = None
    for pattern, kind in DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if kind == "dmy":
                d = date(int(m.group(3)), MONTHS.index(m.group(2).lower()) + 1, int(m.group(1)))
            elif kind == "mdy":
                d = date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2)))
            else:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), d)
    return best[1] if best else None


# ---------------------------------------------------------------------------- HTML

def parse_html(raw: str | bytes, url: str) -> Parsed:
    from bs4 import BeautifulSoup  # Imported here: only needed when HTML is parsed.

    soup = BeautifulSoup(raw, "html.parser")
    notes: list[str] = []

    def meta(*names: str) -> str:
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    # --- Title: og:title > <h1> > <title> (minus " | Site name")
    title = meta("og:title")
    if title:
        notes.append("title from og:title")
    elif soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
        notes.append("title from <h1>")
    else:
        title = (soup.title.get_text(strip=True) if soup.title else url).split(" | ")[0]
        notes.append("title from <title>")

    publisher_hint = meta("og:site_name")

    # --- Date: metadata first, then visible date elements, then any date in the text.
    published: date | None = None
    meta_date = meta("article:published_time", "date", "pubdate")
    if meta_date:
        published = find_date(meta_date[:10])
        notes.append("date from page metadata")
    if not published and soup.find("time", attrs={"datetime": True}):
        published = find_date(soup.find("time")["datetime"][:10])
        notes.append("date from <time> tag")
    if not published:
        for cls in ("byline", "post-meta", "last-updated", "date"):
            el = soup.find(class_=cls)
            if el and (published := find_date(el.get_text(" "))):
                notes.append(f"date from visible '{cls}' text")
                break

    # --- Remove page furniture.
    for tag in soup(BOILERPLATE_TAGS):
        tag.decompose()
    for el in soup.find_all(True):
        if el.attrs is None:  # Already removed as part of a parent.
            continue
        marker = " ".join([*(el.get("class") or []), el.get("id") or ""]).lower()
        if any(h in marker for h in BOILERPLATE_HINTS):
            el.decompose()

    root = soup.find("article") or soup.find("main") or soup.body or soup
    for el in root.find_all(class_=["byline", "post-meta", "section-label", "last-updated"]):
        el.decompose()
    for h1 in root.find_all("h1"):
        h1.decompose()  # The title is stored separately.

    # --- Links inside the content only (menus were removed above).
    links = []
    for a in root.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        links.append(Link(url=urljoin(url, href), text=a.get_text(" ", strip=True)))

    # Keep the page's own spacing inside a block, then collapse runs of whitespace.
    blocks = [re.sub(r"\s+", " ", b.get_text()).strip() for b in root.find_all(["p", "li", "h2", "h3", "td"])]
    text = "\n".join(b for b in blocks if b) or root.get_text("\n", strip=True)

    if not published and (published := find_date(text)):
        notes.append("date guessed from first date in the text (check)")
    if not published:
        notes.append("no date found")
    return Parsed(title=title, text=text, published_date=published, publisher_hint=publisher_hint,
                  links=links, notes=notes)


# ---------------------------------------------------------------------------- PDF

_PAGE_NO = re.compile(r"^\s*(page\s*)?\d+\s*(of\s*\d+)?\s*$", re.IGNORECASE)


def parse_pdf(raw: bytes, url: str) -> Parsed:
    from pypdf import PdfReader  # Imported here: only needed when a PDF is parsed.

    reader = PdfReader(io.BytesIO(raw))
    notes: list[str] = []
    pages, margin_text = [], set()
    for page in reader.pages:
        height = float(page.mediabox.height)

        # Text drawn in the top or bottom 6% of the page is a running header/footer.
        def visitor(text, cm, tm, font_dict, font_size, _h=height):
            y = tm[5] * cm[3] + cm[5] if cm else tm[5]
            if text.strip() and (y < 0.06 * _h or y > 0.94 * _h):
                margin_text.add(text.strip())

        pages.append(page.extract_text(visitor_text=visitor) or "")

    # Lines that repeat on every page (2+ pages) are headers/footers too.
    page_lines = [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]
    repeated = set.intersection(*(set(ls) for ls in page_lines)) if len(page_lines) > 1 else set()

    kept: list[str] = []
    for lines in page_lines:
        for ln in lines:
            if ln in repeated or ln in margin_text or _PAGE_NO.match(ln):
                continue
            kept.append(ln)
    if margin_text:
        notes.append("running header/footer removed")

    # Re-join lines that were wrapped mid-sentence (long line not ending in punctuation).
    merged: list[str] = []
    for ln in kept:
        if merged and len(merged[-1]) > 70 and not merged[-1].endswith((".", ":", ";")):
            merged[-1] = f"{merged[-1]} {ln}"
        else:
            merged.append(ln)
    text = "\n".join(merged)
    if not text.strip():
        notes.append("no text layer (scanned PDF?): needs OCR")

    meta = reader.metadata or {}
    title = (meta.get("/Title") or "").strip() or (merged[0] if merged else url.rsplit("/", 1)[-1])
    notes.append("title from PDF metadata" if meta.get("/Title") else "title from first line")

    published = find_date(text)
    notes.append("date from first date in the document text" if published else "no date found in text")
    notes.append("PDF metadata date ignored (usually the file creation date)")
    return Parsed(title=title, text=text, published_date=published,
                  publisher_hint=(meta.get("/Author") or "").strip(), links=[], notes=notes)


# ---------------------------------------------------------------------------- source type

@lru_cache(maxsize=4)
def load_sources(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def classify_source(url: str, publisher_hint: str, case: CaseCard, sources: dict) -> tuple[str, str, str]:
    """Return (publisher, source_type, note)."""
    parsed = urlparse(url)
    domain, path = _host(url), parsed.path.lower()

    if is_own_site(url, case):
        for rule in sources.get("own_site_path_rules", []):
            if any(fragment in path for fragment in rule["contains"]):
                return case.organisation_name, rule["source_type"], "the organisation's own website"
    known = None
    parts = domain.split(".")
    for i in range(len(parts) - 1):  # news.example.com -> example.com
        known = sources.get("domains", {}).get(".".join(parts[i:]))
        if known:
            break
    if known:
        return known["publisher"], known["source_type"], "known domain (config/sources.json)"
    for rule in sources.get("fallback_rules", []):
        if any(fragment in domain for fragment in rule["domain_contains"]):
            return publisher_hint or domain, rule["source_type"], "domain pattern rule"
    return publisher_hint or domain, "unknown", "unknown domain: low reliability until reviewed"


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_own_site(url: str, case: CaseCard) -> bool:
    """True if the URL is on one of the organisation's own websites (or a subdomain of one)."""
    host = _host(url)
    for site in case.websites:
        site = site.lower().removeprefix("www.")
        if host == site or host.endswith("." + site):
            return True
    return False


# ---------------------------------------------------------------------------- entry point

def build_document(doc_id: str, url: str, content_type: str, raw: bytes, case: CaseCard,
                   sources: dict, retrieved_by: str) -> SourceDocument:
    """Parse raw content of any supported type into the SourceDocument contract."""
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        parsed, ctype = parse_pdf(raw, url), "application/pdf"
    else:
        parsed, ctype = parse_html(raw, url), "text/html"
    publisher, source_type, how = classify_source(url, parsed.publisher_hint, case, sources)
    own_site = is_own_site(url, case)
    return SourceDocument(
        doc_id=doc_id, title=parsed.title, url=url, publisher=publisher, source_type=source_type,
        published_date=parsed.published_date, text=parsed.text, retrieved_by=retrieved_by,
        content_type=ctype, links=parsed.links, parse_notes=[*parsed.notes, f"source type: {how}"],
        origin=Origin.PARTNER if own_site else Origin.THIRD_PARTY,
    )
