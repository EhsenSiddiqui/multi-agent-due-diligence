"""
Builds the FICTIONAL "simulated web" for each demo case:

    data/cases/<case_id>/web/<domain>/<path>.html|.pdf   realistic pages and PDFs
    data/cases/<case_id>/web/index.json                  which URLs exist, and which a search engine has indexed

Why generate instead of hand-writing files?
    - The pages look like real sites (menus, cookie banners, "related stories",
      footers, PDF letterheads), so the parser has real clutter to strip.
    - Anyone on the team can edit the stories below and regenerate:
          python scripts/build_demo_web.py
    - Some pages are NOT indexed by the search engine (indexed=False). They can only
      be reached by following a link from another page, which is what the agent's
      lead-following step has to do (e.g. a regulator's PDF linked from an NGO post).

Every page is fictional. All domains end in .example.
Requires: reportlab (for PDFs).
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "cases"
FICTION = "Fictional page created for The Water Challenge hackathon demo. Not a real organisation."


# =============================================================================
# HTML page builders (three site styles: news, corporate, NGO)
# =============================================================================

def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _links_html(paragraph: str, links: dict[str, str]) -> str:
    """Turn [[anchor text]] markers in a paragraph into <a href> links."""
    out = _e(paragraph)
    for anchor, url in links.items():
        out = out.replace(f"[[{_e(anchor)}]]", f'<a href="{_e(url)}">{_e(anchor)}</a>')
    return out


def news_page(site, section, title, author, date_iso, date_text, paragraphs, links=None,
              related=None, meta_date=True, standfirst=""):
    links = links or {}
    related = related or []
    meta = f'<meta property="article:published_time" content="{date_iso}T07:30:00Z">' if meta_date else ""
    body = "\n".join(f"<p>{_links_html(p, links)}</p>" for p in paragraphs)
    rel = "\n".join(f'<li><a href="#">{_e(r)}</a></li>' for r in related)
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>{_e(title)} | {_e(site)}</title>
<meta property="og:title" content="{_e(title)}">
<meta property="og:site_name" content="{_e(site)}">
{meta}
<script>window.dataLayer=[];function track(e){{dataLayer.push(e)}}</script>
<style>body{{font-family:Georgia,serif;max-width:760px;margin:auto}}</style>
</head><body>
<div class="cookie-banner" id="cookie-consent">We use cookies to improve your experience and to manage advertising risk.
 <button>Accept all</button> <button>Manage preferences</button></div>
<header class="site-header"><a class="logo" href="/">{_e(site)}</a>
 <nav class="main-nav"><a href="/news">News</a> <a href="/business">Business</a> <a href="/markets">Markets</a>
 <a href="/opinion">Opinion</a> <a href="/subscribe">Subscribe</a></nav></header>
<div class="ad-slot leaderboard">ADVERTISEMENT</div>
<main><article>
 <p class="section-label">{_e(section)}</p>
 <h1>{_e(title)}</h1>
 {f'<p class="standfirst">{_e(standfirst)}</p>' if standfirst else ''}
 <p class="byline">By {_e(author)} &middot; <time datetime="{date_iso}">{_e(date_text)}</time></p>
 {body}
 <div class="newsletter-signup">Get the morning briefing. Sign up for free newsletters on markets, risk and regulation.</div>
</article>
<aside class="related-stories"><h2>Related stories</h2><ul>{rel}</ul></aside>
<div class="ad-slot sidebar">ADVERTISEMENT</div></main>
<footer class="site-footer"><p>&copy; {date_iso[:4]} {_e(site)}. All rights reserved.
 <a href="/privacy">Privacy</a> <a href="/terms">Terms</a></p><p class="fiction-note">{FICTION}</p></footer>
</body></html>"""


def corporate_page(company, title, paragraphs, links=None, updated_text="", date_iso=""):
    links = links or {}
    body = "\n".join(f"<p>{_links_html(p, links)}</p>" for p in paragraphs)
    updated = f'<p class="last-updated">{_e(updated_text)}</p>' if updated_text else ""
    meta = f'<meta name="date" content="{date_iso}">' if date_iso else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_e(title)} | {_e(company)}</title>
<meta property="og:site_name" content="{_e(company)}">{meta}</head><body>
<header><div class="logo">{_e(company)}</div>
<nav><ul><li><a href="/">Home</a></li><li><a href="/services">What we do</a></li><li><a href="/sustainability">Sustainability</a></li>
<li><a href="/news">News</a></li><li><a href="/careers">Careers</a></li><li><a href="/contact">Contact</a></li></ul></nav></header>
<section class="hero"><h1>{_e(title)}</h1></section>
<main><div class="content">{body}{updated}</div></main>
<footer><p>&copy; {_e(company)}. <a href="/legal">Legal</a> | <a href="/cookies">Cookie policy</a></p>
<p class="fiction-note">{FICTION}</p></footer></body></html>"""


def ngo_page(org, title, date_iso, date_text, paragraphs, links=None):
    links = links or {}
    body = "\n".join(f"<p>{_links_html(p, links)}</p>" for p in paragraphs)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_e(title)} - {_e(org)}</title>
<meta property="og:site_name" content="{_e(org)}"></head><body>
<div class="donate-bar">Donate today: every gift funds our community campaigns. <a href="/donate">Give now</a></div>
<header><h2>{_e(org)}</h2><nav><a href="/campaigns">Campaigns</a> <a href="/blog">Blog</a> <a href="/about">About</a></nav></header>
<main><article class="post"><h1>{_e(title)}</h1>
<div class="post-meta">Posted on <span class="date">{_e(date_text)}</span> in Campaigns</div>
{body}
<div class="share-buttons">Share: Facebook | X | WhatsApp</div></article>
<div class="comments"><h3>2 comments</h3><p>Great work, keep it up!</p><p>Shared with my union branch.</p></div></main>
<footer><p>{_e(org)} is a registered non-profit.</p><p class="fiction-note">{FICTION}</p></footer></body></html>"""


# =============================================================================
# PDF builder (letterhead, reference block, numbered paragraphs, footer)
# =============================================================================

def build_pdf(path: Path, letterhead: str, sub_letterhead: str, doc_title: str, ref_rows, paragraphs,
              footer: str, pdf_title: str, subject: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    head = ParagraphStyle("head", parent=styles["Title"], fontSize=14, alignment=0, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=8)
    title_style = ParagraphStyle("t", parent=styles["Heading2"], fontSize=12, spaceBefore=10)

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, f"{footer}  |  {FICTION}")
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    story = [Paragraph(letterhead, head), Paragraph(sub_letterhead, sub), Spacer(1, 6)]
    table = Table(ref_rows, colWidths=[40 * mm, 120 * mm])
    table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                               ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.grey)]))
    story += [table, Paragraph(doc_title, title_style)]
    story += [Paragraph(p, body) for p in paragraphs]
    # Metadata creation date is the BUILD date, not the document date: a realistic trap for the parser.
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=pdf_title, author=letterhead, subject=subject,
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


# =============================================================================
# The three fictional cases
# =============================================================================

def case_a():
    """Clean local supplier. One code-of-conduct PDF reachable only from the About page."""
    pages = []
    pages.append(("A1", "https://brightwell-supplies.example/about", True, corporate_page(
        "Brightwell Office Supplies Ltd.", "About Brightwell", [
            "Brightwell Office Supplies Ltd. is a family-owned stationery business founded in 1998 by Dana and Eric Kim.",
            "We run one warehouse in Mississauga, Ontario, and deliver to offices across the Greater Toronto Area. "
            "All products are bought from Canadian distributors.",
            "Our 40 workers are paid above the Ontario living wage. Read our [[Supplier Code of Conduct (PDF)]].",
        ], links={"Supplier Code of Conduct (PDF)": "https://brightwell-supplies.example/docs/supplier-code-of-conduct.pdf"},
        updated_text="Last updated 15 January 2025")))
    pages.append(("A2", "https://mississauga-local.example/news/2025/04/22/brightwell-community-award", True, news_page(
        "Mississauga Local Times", "Community", "Brightwell wins small business award for school supply drive",
        "Priya Nair", "2025-04-22", "April 22, 2025", [
            "Brightwell, the Mississauga stationery supplier, received the city's Small Business Community Award on Monday.",
            "Staff and workers volunteered on weekends to pack 3,000 backpacks for local schools.",
            "\"We take no risk with quality and we buy locally,\" said owner Dana Kim.",
        ], related=["City launches investigation into transit contract fraud",
                    "Councillor accused of bribery in zoning row"])))
    build_pdf_spec = ("A3", "https://brightwell-supplies.example/docs/supplier-code-of-conduct.pdf", False, {
        "letterhead": "Brightwell Office Supplies Ltd.", "sub": "2210 Dundas Street East, Mississauga, ON",
        "title": "Supplier Code of Conduct", "ref": [["Document", "BOS-POL-004"], ["Version", "3.1"],
                                                     ["Date", "1 September 2024"]],
        "paras": ["1. Every distributor must respect labour standards and Ontario environmental law.",
                  "2. Brightwell reviews supplier risk once a year and may visit warehouses without notice.",
                  "3. Paper products must contain at least 50 percent recycled content."],
        "footer": "Brightwell Office Supplies Ltd. - Supplier Code of Conduct v3.1", "pdf_title": "Supplier Code of Conduct"})
    return pages, [build_pdf_spec]


def case_b():
    """2019 safety fine (court PDF only linked from the news story), weak NGO allegation."""
    pages = []
    pages.append(("B1", "https://northstream-apparel.example/our-story", True, corporate_page(
        "Northstream Apparel Inc.", "Our story", [
            "Northstream Apparel Inc. designs outdoor clothing in Toronto for hikers, paddlers and city commuters.",
            "Our garments are made by partner factories in Bangladesh and Vietnam, which we audit every year against our supplier code.",
            "Read our [[statement on workplace safety]].",
        ], links={"statement on workplace safety": "https://northstream-apparel.example/news/2019-safety-statement"},
        updated_text="Last updated 10 February 2025")))
    pages.append(("B2", "https://toronto-business-daily.example/2019/03/14/northstream-apparel-fined", True, news_page(
        "Toronto Business Daily", "Business", "Ministry of Labour fines Northstream Apparel after warehouse injury",
        "Sam Okafor", "2019-03-14", "Thursday, March 14, 2019", [
            "Northstream Apparel Inc. was fined $45,000 after a warehouse worker was injured by an unguarded conveyor in 2018.",
            "The Toronto clothing brand pleaded guilty to one charge under the Occupational Health and Safety Act, "
            "according to the [[Ontario Court of Justice bulletin]].",
            "The company said it has since fitted guards to every conveyor.",
        ], links={"Ontario Court of Justice bulletin": "https://ontario-courts.example/bulletins/2019/0311-northstream.pdf"},
        related=["Fraud charges laid in condo deposit scheme", "Retail sales slow in February"])))
    pages.append(("B4", "https://northstream-apparel.example/news/2019-safety-statement", True, corporate_page(
        "Northstream Apparel Inc.", "Statement on the 2019 safety fine", [
            "Northstream paid the fine in full and has resolved the issue.",
            "Every conveyor now has guards, and all warehouse staff complete annual safety training.",
        ], updated_text="Published 2 April 2019", date_iso="2019-04-02")))
    pages.append(("B5", "https://cleanthreads-campaign.example/blog/brands-still-profiting-from-poverty-pay", True, ngo_page(
        "Clean Threads Campaign", "Brands still profiting from poverty pay", "2023-06-08", "8 June 2023", [
            "Clean Threads alleges that several Canadian brands, including Northstream, still source from Bangladeshi "
            "factories where unfair wages are common.",
            "We have not published factory names because workers fear retaliation. Northstream did not reply to our request for comment.",
        ])))
    pages.append(("B6", "https://gear-review.example/2024/10/northstream-recycled-shell-jacket", True, news_page(
        "Gear Review Weekly", "Reviews", "Northstream launches recycled shell jacket", "Jo Lindqvist",
        "2024-10-03", "3 October 2024", [
            "Northstream's new jacket uses 80 percent recycled polyester and weighs 310 grams.",
            "The brand says the launch carries little risk because demand for recycled outdoor gear is growing.",
        ], meta_date=False)))
    pdf = ("B3", "https://ontario-courts.example/bulletins/2019/0311-northstream.pdf", False, {
        "letterhead": "Ontario Court of Justice", "sub": "Provincial Offences Bulletin",
        "title": "R. v. Northstream Apparel Inc.", "ref": [["Court file", "4811-998-18-0217"], ["Location", "Toronto (Etobicoke)"],
                                                          ["Date", "11 March 2019"]],
        "paras": ["Northstream Apparel Inc. pleaded guilty to one count under section 25(1)(a) of the Occupational Health and Safety Act.",
                  "The court ordered the company to pay a fine of $45,000 plus the victim fine surcharge.",
                  "The charge arose from an injury to a worker at the company's Etobicoke warehouse in 2018."],
        "footer": "Ontario Court of Justice - Provincial Offences Bulletin", "pdf_title": "Bulletin 2019-0311"})
    return pages, [pdf]


def case_c():
    """Disputed divestment, regulator PDF only reachable via links, NGO campaign, a namesake."""
    pages = []
    pages.append(("C1", "https://corvane-group.example/who-we-are", True, corporate_page(
        "Corvane Group Holdings Ltd.", "Who we are", [
            "Corvane Group provides engineering and infrastructure services across the Americas.",
            "Group companies: Corvane Engineering Ltd. (Canada), Corvane Infra Chile SpA (Chile) and "
            "Corvane Minerals S.A. (mineral exploration services, Peru).",
            "Download our [[2023 Annual Review (PDF)]] or read the [[statement on Huarani]].",
        ], links={"2023 Annual Review (PDF)": "https://corvane-group.example/reports/2023-annual-review.pdf",
                  "statement on Huarani": "https://corvane-group.example/news/huarani-statement"},
        updated_text="Last updated 30 May 2023")))
    pages.append(("C2", "https://andean-business-review.example/2024/02/19/corvane-to-sell-exploration-arm", True, news_page(
        "Andean Business Review", "Mining & Energy", "Corvane to sell exploration arm to Altiplano Resources",
        "Lucia Ferrer", "2024-02-19", "19 February 2024", [
            "Corvane Group has agreed to sell its mining exploration unit, Corvane Minerals S.A., to Altiplano Resources.",
            "The sale is expected to close later in 2024, subject to approval by Peruvian authorities.",
            "Terms were not disclosed.",
        ], related=["Copper miners face bribery probe in Arequipa", "Lithium prices slide for third month"])))
    pages.append(("C4", "https://aguaviva-collective.example/2025/09/huarani-families-water", True, ngo_page(
        "Agua Viva Collective", "Huarani families say their stream is no longer safe", "2025-09-03", "3 September 2025", [
            "Families in Huarani say the stream they use for drinking water has turned cloudy since Corvane Minerales began drilling.",
            "Agua Viva alleges contamination and calls on international donors to cut ties with Corvane until the water is tested.",
            "The environmental regulator has opened a case: see the [[OEFA notice RD 0457-2025 (PDF)]].",
        ], links={"OEFA notice RD 0457-2025 (PDF)": "https://oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf"})))
    pages.append(("C5", "https://corvane-group.example/news/huarani-statement", True, corporate_page(
        "Corvane Group Holdings Ltd.", "Corvane Group statement on Huarani reports", [
            "Corvane Minerals S.A. has not been part of Corvane Group since the sale to Altiplano Resources was completed in December 2024.",
            "Corvane Group rejects any suggestion that it is responsible for conditions at Huarani.",
        ], updated_text="Published 10 September 2025", date_iso="2025-09-10")))
    pages.append(("C6", "https://lima-economic-daily.example/2025/10/21/altiplano-corvane-deal-awaits-approval", True, news_page(
        "Lima Economic Daily", "Companies", "Altiplano deal for Corvane unit still awaiting approval", "Diego Salas",
        "2025-10-21", "21 October 2025", [
            "Corvane Minerales, the exploration unit of Canada's Corvane Group, remains under Corvane ownership "
            "because the sale to Altiplano Resources is still waiting for a mining registry approval, according to two people familiar with the deal.",
            "Altiplano gave a new timetable in an [[investor update]] this month. The unit is also named in a "
            "[[regulator's notice]] about the Huarani camp.",
        ], links={"investor update": "https://altiplano-resources.example/investors/2025-11-04-update",
                  "regulator's notice": "https://oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf"},
        standfirst="Registry delay leaves ownership of the Huarani exploration company unchanged")))
    pages.append(("C7", "https://halifax-herald.example/2026/01/08/dental-clinic-billing-fraud-fine", True, news_page(
        "Halifax Herald", "Local", "Halifax clinic fined over billing fraud", "Mark Doucette", "2026-01-08",
        "January 8, 2026", [
            "Corvane Dental Clinic in Halifax was fined $12,000 after an audit found billing fraud.",
            "The clinic is owned by two local dentists.",
        ])))
    pages.append(("C9", "https://altiplano-resources.example/investors/2025-11-04-update", False, corporate_page(
        "Altiplano Resources Inc.", "Investor update: Huarani acquisition", [
            "Altiplano Resources Inc. expects to complete its acquisition of Corvane Minerales S.A.C. from Corvane Group "
            "Holdings Ltd. in the first quarter of 2026, once the transfer is entered in Peru's mining registry.",
            "Until completion, the Huarani exploration company remains owned by its current parent.",
        ], updated_text="4 November 2025", date_iso="2025-11-04")))
    pdfs = [
        ("C3", "https://oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf", False, {
            "letterhead": "OEFA", "sub": "Environmental Assessment and Enforcement Agency (Peru) - unofficial English translation",
            "title": "Notice of administrative proceedings", "ref": [["Resolution", "RD 0457-2025-OEFA/DFAI"],
                                                                    ["Date", "12 August 2025"],
                                                                    ["Respondent", "Corvane Minerales S.A.C."]],
            "paras": ["OEFA has opened an administrative investigation into Corvane Minerales S.A.C. for possible discharge of "
                      "untreated drilling water into a tributary of the Chillon river near the Huarani exploration camp.",
                      "No sanction has been decided. The respondent may submit its defence within 20 working days."],
            "footer": "OEFA - RD 0457-2025", "pdf_title": "RD 0457-2025"}),
        ("C8", "https://corvane-group.example/reports/2023-annual-review.pdf", False, {
            "letterhead": "Corvane Group Holdings Ltd.", "sub": "Annual Review 2023 (excerpt)",
            "title": "Our year in numbers", "ref": [["Report", "Annual Review 2023"], ["Date", "28 March 2024"]],
            "paras": ["Revenue in 2023 came 61 percent from Canada, 24 percent from Chile and 15 percent from Peru, "
                      "where our mineral exploration services support third-party mining projects.",
                      "Headcount grew to 1,240 people across three countries."],
            "footer": "Corvane Group - Annual Review 2023", "pdf_title": "Annual Review 2023"}),
    ]
    return pages, pdfs


# =============================================================================
# Write everything
# =============================================================================

def url_to_path(web: Path, url: str) -> Path:
    """https://site.example/a/b  ->  web/site.example/a/b.html (keeps .pdf as is)."""
    rest = url.split("://", 1)[1]
    p = web / rest
    return p if p.suffix in (".pdf", ".html") else p.with_suffix(p.suffix + ".html") if p.suffix else Path(str(p) + ".html")


ORIGIN_NOTES = {
    "partner_organisation": "Published by the partner organisation itself (its own website or documents).",
    "third_party_public_source": "Published by a third party (news outlet, regulator, court, NGO or other company).",
}


def origin_for(url: str, case_id: str) -> str:
    """Pages on the organisation's own domains (case card 'websites') are partner material."""
    websites = json.loads((CASES / case_id / "case.json").read_text(encoding="utf-8")).get("websites", [])
    domain = url.split("://", 1)[1].split("/", 1)[0]
    return "partner_organisation" if domain in websites else "third_party_public_source"


def label_html(content: str, origin: str) -> str:
    """Add a provenance comment at the top of the page. Comments are ignored by the scraper."""
    return content.replace("<!DOCTYPE html>", f"<!DOCTYPE html>\n<!-- PROVENANCE: {origin}. {ORIGIN_NOTES[origin]} "
                                              f"Fictional demo page. -->", 1)


def write_case(case_id: str, builder) -> None:
    web = CASES / case_id / "web"
    if web.exists():
        shutil.rmtree(web)
    pages, pdfs = builder()
    index = []
    for doc_id, url, indexed, content in pages:
        path = url_to_path(web, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        origin = origin_for(url, case_id)
        path.write_text(label_html(content, origin), encoding="utf-8")
        index.append({"id": doc_id, "url": url, "file": path.relative_to(web).as_posix(),
                      "content_type": "text/html", "indexed": indexed, "origin": origin})
    for doc_id, url, indexed, spec in pdfs:
        path = url_to_path(web, url)
        origin = origin_for(url, case_id)
        build_pdf(path, spec["letterhead"], spec["sub"], spec["title"], spec["ref"], spec["paras"],
                  spec["footer"], spec["pdf_title"], subject=f"PROVENANCE: {origin}. {ORIGIN_NOTES[origin]}")
        index.append({"id": doc_id, "url": url, "file": path.relative_to(web).as_posix(),
                      "content_type": "application/pdf", "indexed": indexed, "origin": origin})
    index.sort(key=lambda r: r["id"])
    (web / "index.json").write_text(json.dumps({
        "_about": "Simulated web for this fictional case. 'indexed': false pages can only be reached by following links. "
                  "'origin' says who published each page: partner_organisation or third_party_public_source.",
        "built": datetime.now().strftime("%Y-%m-%d"), "pages": index}, indent=2), encoding="utf-8")
    print(f"{case_id}: {len(index)} pages ({sum(not r['indexed'] for r in index)} link-only)")


if __name__ == "__main__":
    write_case("case_a_brightwell", case_a)
    write_case("case_b_northstream", case_b)
    write_case("case_c_corvane", case_c)
