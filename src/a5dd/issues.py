"""
Issue timelines: turns loose evidence rows into ISSUES, each with a story in time order.

WHY
    News about a partner arrives from many years and many outlets. "NGO alleges pollution (2019)",
    "regulator opens investigation (2021)", "company fined (2023)" and "clean-up completed (2024)"
    are one issue, and what matters for the decision is where it stands NOW. Twenty articles
    repeating one NGO report are one allegation, not twenty.

STEPS (all plain code, the same every run)
    1. event_of(item):   what happened (event type) and WHEN IT HAPPENED (event date; the
                         publication date is used only if the source gives no event date).
    2. group():          items about the same issue are linked: same issue label words, or a shared
                         proper name (a mine, a river, a project), or (rule mode) the same risk topic.
    3. status:           a small state machine. The current status is the furthest stage reached
                         (a later allegation does not undo an open investigation; an appeal after an
                         outcome reopens it):
                         reported -> under investigation -> charged/sued -> appealed -> outcome
                         (ruling, fine, settlement, dismissed) -> remediated.
    4. origin:           the earliest event and who reported it ("first reported by ...").
    5. independent_sources: distinct publishers, so repetition does not inflate an issue.
    6. serious:          an authority is involved, or the allegation is credible or repeated, within
                         the look-back window. Serious issues with no outcome are what the tracer
                         agent goes after, and what the report flags.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schemas import EvidenceItem, EvidenceStatus, Issue, IssueEvent, Reliability

# event type -> (stage in the story, label). Stage 0 = does not change the status.
EVENT_TYPES = {
    "allegation": (1, "Reported (alleged)"),
    "investigation_opened": (2, "Under investigation"),
    "charges_or_lawsuit": (3, "Charged or sued"),
    "appeal": (3, "Under appeal"),
    "ruling": (4, "Ruled on"),
    "fine": (4, "Fined"),
    "settlement": (4, "Settled"),
    "dismissed": (4, "Dismissed or withdrawn"),
    "remediation": (5, "Remediated"),
    "company_response": (0, "Company responded"),
    "background": (0, "Background"),
}
OUTCOMES = {"ruling", "fine", "settlement", "dismissed"}
AUTHORITY_EVENTS = {"investigation_opened", "charges_or_lawsuit", "appeal", "ruling", "fine", "settlement", "dismissed"}
GENERIC = set("""the and of for in on at by to a an mine mines project plant site river company group corporation
inc ltd limited holdings sa sac plc llc report reports alleged allegations allegation case issue issues""".split())


def status_label(event_type: str) -> str:
    return EVENT_TYPES.get(event_type, (0, event_type.replace("_", " ").capitalize()))[1]


def event_of(item: EvidenceItem) -> str:
    """The analyst's event type, or one derived from the evidence status (rule mode)."""
    if item.event_type in EVENT_TYPES:
        return item.event_type
    q = item.quote.lower()
    return {
        EvidenceStatus.ALLEGATION: "allegation",
        EvidenceStatus.INVESTIGATION: "investigation_opened",
        EvidenceStatus.FINDING: ("settlement" if "settle" in q else "fine" if "fine" in q or "penalt" in q else "ruling"),
        EvidenceStatus.RESPONSE: "company_response",
        EvidenceStatus.CONTEXT: "background",
    }[item.status]


def _date(item: EvidenceItem) -> tuple[str | None, bool]:
    if item.event_date and re.match(r"^\d{4}(-\d{2}){0,2}$", item.event_date):
        return item.event_date, False
    return (item.published_date.isoformat() if item.published_date else None), True


def _label(item: EvidenceItem) -> str:
    """The analyst's issue label, or (rule mode) a label from the main risk topic and the name used."""
    if item.issue:
        return item.issue
    topics = [t for t in item.topics if not t.startswith(("country:", "sector:", "event_year:"))]
    for main in ("environment", "water", "corruption", "labour", "wages", "health_safety", "political", "divestment"):
        if main in topics:
            topic = "water and environment" if main in ("environment", "water") else main.replace("_", " and ")
            return f"{topic.capitalize()}"
    return ""


def _proper_names(label: str, org_words: set[str]) -> set[str]:
    return {w.lower() for w in re.findall(r"\b[A-Z][\w-]{3,}", label)} - org_words - GENERIC


def _words(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", label.lower()) if w not in GENERIC and len(w) > 2}


def _same_issue(a: str, b: str, org_words: set[str]) -> bool:
    if not a or not b:
        return False
    if a.lower() == b.lower() or SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.75:
        return True
    if _proper_names(a, org_words) & _proper_names(b, org_words):
        return True
    wa, wb = _words(a) - org_words, _words(b) - org_words
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5


def build_issues(items: list[EvidenceItem], org_name: str, aliases: list[str], assessment_year: int,
                 lookback_years: int) -> list[Issue]:
    """Group evidence into issues and work out each issue's timeline and current status."""
    org_words = {w.lower() for n in [org_name, *aliases] for w in re.findall(r"[\w-]+", n)}
    candidates = [i for i in items if event_of(i) != "background" or i.issue]
    labels = [_label(i) for i in candidates]

    # Union-find over items that describe the same issue.
    parent = list(range(len(candidates)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(candidates)):
        for b in range(a + 1, len(candidates)):
            if _same_issue(labels[a], labels[b], org_words):
                parent[find(a)] = find(b)
    # A company response with no label of its own joins the issue it shares most topics with.
    for a, item in enumerate(candidates):
        if not labels[a] and event_of(item) == "company_response":
            best = max((b for b in range(len(candidates)) if labels[b]),
                       key=lambda b: len(set(item.topics) & set(candidates[b].topics)), default=None)
            if best is not None and set(item.topics) & set(candidates[best].topics):
                parent[find(a)] = find(best)

    groups: dict[int, list[int]] = {}
    for a in range(len(candidates)):
        groups.setdefault(find(a), []).append(a)

    issues = []
    for members in groups.values():
        its = [candidates[m] for m in members]
        if all(event_of(i) in ("company_response", "background") for i in its):
            continue  # A statement with nothing to respond to is not an issue.
        events = []
        for i in its:
            date, is_pub = _date(i)
            events.append(IssueEvent(date=date, date_is_publication=is_pub, event_type=event_of(i),
                                     evidence_id=i.evidence_id, publisher=i.publisher, reliability=i.reliability,
                                     summary=i.summary))
        events.sort(key=lambda e: (e.date or "9999", EVENT_TYPES.get(e.event_type, (0,))[0]))
        changing = [e for e in events if EVENT_TYPES.get(e.event_type, (0,))[0] > 0]
        # The story only moves forward (a later allegation does not undo an open investigation),
        # except that an appeal filed after an outcome reopens it.
        current = max(changing, key=lambda e: (EVENT_TYPES[e.event_type][0], e.date or "")) if changing else events[-1]
        appeals = [e for e in changing if e.event_type == "appeal" and (e.date or "") > (current.date or "")]
        if current.event_type in OUTCOMES and appeals:
            current = appeals[-1]
        first = changing[0] if changing else events[0]
        publishers = {e.publisher for e in events if e.event_type != "company_response"}
        latest_year = max((int(e.date[:4]) for e in events if e.date), default=assessment_year)
        authority = any(e.event_type in AUTHORITY_EVENTS for e in events)
        credible = any(e.reliability != Reliability.LOW for e in events if e.event_type == "allegation")
        label = max((labels[m] for m in members), key=len) or "Unnamed issue"
        issues.append(Issue(
            issue_id="", label=label,
            question_ids=sorted({q for i in its for q in i.question_ids}),
            events=events,
            current_status=current.event_type,
            outcome_found=any(e.event_type in OUTCOMES for e in events),
            first_reported=f"{first.publisher}, {first.date or 'undated'}",
            independent_sources=len(publishers),
            serious=(latest_year >= assessment_year - lookback_years) and (authority or credible or len(publishers) >= 2),
        ))
    issues.sort(key=lambda s: (not s.serious, s.outcome_found, -(EVENT_TYPES.get(s.current_status, (0,))[0]), s.label))
    for n, issue in enumerate(issues, start=1):
        issue.issue_id = f"I{n}"
    return issues


def needs_tracing(issue: Issue) -> bool:
    """Serious, and either no outcome found or the story starts in a secondary source."""
    return issue.serious and (not issue.outcome_found or issue.independent_sources <= 1)
