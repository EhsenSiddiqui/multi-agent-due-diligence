"""
Data contracts for the A5 due-diligence pipeline.

WHY THIS FILE MATTERS
---------------------
Every stage of the pipeline talks to the next stage ONLY through the models
defined here, written to disk as JSON. No stage imports another stage.

That gives us the rule the team asked for:
    "If we improve one file, the other files do not need to change."

As long as a new implementation still produces these models (same field names,
same allowed values), every downstream stage keeps working. For example, the
offline simulated-web search and a live web-search API both return a
`SearchResults` object, so stage 3 never knows which one ran.

VERSIONING
----------
Each stage output carries `schema_version`. If a field is ever renamed or a
meaning changes, bump SCHEMA_VERSION and update the readers. Adding a NEW
optional field is backwards compatible and does not need a version bump.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Bump only for breaking changes (renamed/removed fields, changed meanings).
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Controlled vocabularies (enums). Using enums instead of free text means a
# typo like "Allegaton" fails validation immediately instead of silently
# producing a wrong count later.
# ---------------------------------------------------------------------------

class EvidenceStatus(str, Enum):
    """How established a piece of evidence is. This is the core A5 distinction."""
    ALLEGATION = "allegation"        # Someone claims it; nothing established.
    INVESTIGATION = "investigation"  # An authority is looking into it.
    FINDING = "finding"              # Established: fine, ruling, conviction, settlement.
    RESPONSE = "response"            # The organisation's own reply or statement.
    CONTEXT = "context"              # Neutral facts (ownership, countries, policies).


class Reliability(str, Enum):
    """How much weight a source deserves (set from source type in config)."""
    HIGH = "high"      # Regulator, court, official filing.
    MEDIUM = "medium"  # Established news outlet, the company's own published report.
    LOW = "low"        # Blog, social media, campaign site, unattributed.


class RiskCall(str, Enum):
    """Answer to 'Potential risk?' for one core question (template b)."""
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"  # Conflicting or thin evidence: goes to staff judgement.


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EntityMatch(str, Enum):
    """Is this document really about our organisation?"""
    EXACT = "exact"          # Exact legal or trading name.
    ALIAS = "alias"          # Known subsidiary, former name or listed alias.
    UNCERTAIN = "uncertain"  # Similar name only; a human must confirm.
    NONE = "none"            # Different organisation (e.g. a namesake).


class Origin(str, Enum):
    """WHO prepared a piece of information. Shown on every file and every evidence row."""
    PARTNER = "partner_organisation"           # Published or provided by the organisation being checked.
    THIRD_PARTY = "third_party_public_source"  # Published by someone else: news, regulator, court, NGO.
    STAFF = "water_care_staff"                 # Entered or decided by a person at Water Care.
    AI_AGENT = "ai_agent"                      # Produced automatically by the due-diligence agent.


ORIGIN_LABELS = {
    Origin.PARTNER: "Partner organisation",
    Origin.THIRD_PARTY: "Third-party public source",
    Origin.STAFF: "Water Care staff",
    Origin.AI_AGENT: "AI agent",
}


class Provenance(BaseModel):
    """Who prepared a file, and whether a person has reviewed it."""
    origin: Origin
    prepared_by: str
    human_reviewed: bool = False
    note: str = ""


def agent_provenance(note: str = "") -> Provenance:
    """Default label for anything the tool produces."""
    return Provenance(
        origin=Origin.AI_AGENT,
        prepared_by="A5 due-diligence agent (automated pipeline)",
        human_reviewed=False,
        note=note or "Automated draft. Must be checked by Water Care staff before any decision.",
    )


# ---------------------------------------------------------------------------
# Shared header for every stage output file.
# ---------------------------------------------------------------------------

class StageHeader(BaseModel):
    schema_version: str = SCHEMA_VERSION
    stage: str                      # e.g. "02_search"
    case_id: str
    producer: str                   # Which implementation produced it, e.g. "LocalCorpusSearch".
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    fictional_data: bool = True     # Always shown in reports so nobody mistakes demo data for real.
    provenance: Provenance = Field(default_factory=agent_provenance)  # Who prepared this file.


# ---------------------------------------------------------------------------
# INPUT: the case card (what staff type in about the potential partner).
# ---------------------------------------------------------------------------

class CaseCard(BaseModel):
    case_id: str
    organisation_name: str
    search_name: str = ""              # Name to put in searches, e.g. "Barrick" (empty = organisation_name).
    aliases: list[str] = []            # Former names, trading names, known subsidiaries.
    relationship_type: str             # Must match a key in criteria.json "entry_criteria".
    annual_value_usd: float = 0
    presence: str = "local"            # "local" | "national" | "multinational"
    brand_affiliation: bool = False
    declared_sectors: list[str] = []   # What staff already know, e.g. ["retail"].
    countries_of_operation: list[str] = []
    websites: list[str] = []           # The organisation's own domains, e.g. ["corvane-group.example"].
    notes: str = ""
    fictional: bool = True
    provenance: Provenance = Field(default_factory=lambda: Provenance(
        origin=Origin.STAFF, prepared_by="Relationship lead", note="Case details entered by Water Care staff."))


# ---------------------------------------------------------------------------
# STAGE 1 output: intake
# ---------------------------------------------------------------------------

class IntakeResult(StageHeader):
    case: CaseCard
    check_required: bool
    check_reason: str
    excluded_industry_flags: list[str] = []   # From declared sectors only (stage 4 checks evidence).
    high_risk_area_flags: list[str] = []


# ---------------------------------------------------------------------------
# STAGE 2 output: search
# ---------------------------------------------------------------------------

class Link(BaseModel):
    """A hyperlink found in a page. The agent uses these to reach primary sources."""
    url: str
    text: str = ""


class SourceDocument(BaseModel):
    """One retrieved source. Same shape whether it came from a local file or the web.
    `text` is CLEAN text: menus, cookie banners, ads and related-story lists already removed."""
    doc_id: str
    title: str
    url: str
    publisher: str
    source_type: str                 # e.g. "news", "regulator", "ngo", "company_statement", "company_website".
    published_date: Optional[date] = None
    text: str
    retrieved_by: str                # Implementation name, for the audit trail.
    content_type: str = ""           # "text/html", "application/pdf", ...
    links: list[Link] = []           # Outgoing links (used by the agent's lead-following).
    parse_notes: list[str] = []      # How title/date/publisher were found, e.g. "date from <time> tag".
    origin: Origin = Origin.THIRD_PARTY  # PARTNER if published on the organisation's own website.


class QueryLog(BaseModel):
    """One action the search step took: a search, or (agent) following a link.
    Feeds the coverage log, template b's checklist and the agent log."""
    query: str                       # Query text, or the URL that was followed.
    term: str                        # Template b term, "(profile)", "(follow link)" or "(follow-up search)".
    hit_doc_ids: list[str] = []
    error: Optional[str] = None      # Filled when a source could not be reached.
    round: int = 0                   # 0 = the fixed guide 1.3 protocol; 1+ = agent follow-up rounds.
    action: str = "search"           # "search" or "follow_link".
    reason: str = ""                 # Why the agent chose this action (empty for the fixed protocol).


class SeenResult(BaseModel):
    """A search result that was listed but not opened (title and snippet only)."""
    title: str
    url: str
    snippet: str = ""
    query: str = ""


class SearchResults(StageHeader):
    queries: list[QueryLog]
    documents: list[SourceDocument]  # Unique documents across all queries.
    coverage_notes: list[str] = []   # What this search provider could NOT cover (feeds the gaps list).
    seen_not_opened: list[SeenResult] = []  # Results the fixed searches listed but did not open.
    research_summary: str = ""       # AI researcher's own summary (AI mode only).
    open_questions: list[str] = []   # What the AI researcher could not establish (AI mode only).


# ---------------------------------------------------------------------------
# STAGE 3 output: evidence table
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    evidence_id: str
    doc_id: str
    source_title: str
    source_url: str
    publisher: str
    source_type: str
    published_date: Optional[date] = None
    reliability: Reliability
    entity_match: EntityMatch
    status: EvidenceStatus
    question_ids: list[str]          # Which of Q1..Q6 this bears on.
    standards: list[str]             # "S1" (WASH barriers) and/or "S2" (reputation).
    topics: list[str] = []           # Free tags, e.g. ["labour", "extractives"].
    summary: str                     # One line, careful language.
    quote: str                       # Exact text from the source (validated in stage 3).
    resolved: Optional[bool] = None  # True if the source says the matter was resolved.
    extracted_by: str
    origin: Optional[Origin] = None  # Who published the SOURCE (partner or third party); set by stage 3.
    named_entity: str = ""           # The name the source actually uses (may differ from the organisation's).
    # "Safe to infer" discipline: what this source licenses us to say, and what it does not.
    safe_to_infer: str = ""
    not_safe_to_infer: list[str] = []
    # Timeline fields: what happened, when it HAPPENED (not when it was published), and which issue.
    event_type: str = ""             # See issues.EVENT_TYPES, e.g. "allegation", "investigation_opened", "fine".
    event_date: Optional[str] = None # "YYYY", "YYYY-MM" or "YYYY-MM-DD"; None if the source does not say.
    issue: str = ""                  # Short label naming place/project + problem, e.g. "Huarani camp: river pollution".


class RejectedItem(BaseModel):
    doc_id: str
    reason: str


class IssueEvent(BaseModel):
    """One step in an issue's story, backed by one evidence item."""
    date: Optional[str] = None       # Event date if known, else the source's publication date.
    date_is_publication: bool = False
    event_type: str
    evidence_id: str
    publisher: str
    reliability: Reliability
    summary: str


class Issue(BaseModel):
    """Everything found about one issue, in time order, with its current status."""
    issue_id: str                    # "I1", "I2", ...
    label: str
    question_ids: list[str] = []
    events: list[IssueEvent] = []    # Oldest first.
    current_status: str              # The latest status-changing event, e.g. "investigation_opened", "fine".
    outcome_found: bool = False      # A ruling, fine, settlement or dismissal was found.
    first_reported: str = ""         # "Agua Viva Collective, 2025-09" (origin of the story).
    independent_sources: int = 0     # Distinct publishers; many articles repeating one report count once.
    serious: bool = False            # Worth tracing: an authority is involved, or a credible/repeated allegation.
    traced: bool = False             # The tracer agent tried to find the origin and the outcome.
    trace_notes: str = ""


class EvidenceTable(StageHeader):
    items: list[EvidenceItem]
    rejected: list[RejectedItem] = []  # Kept for transparency: what was thrown out and why.
    issues: list[Issue] = []           # Evidence grouped into issues, each with a timeline and status.
    trace_log: list[QueryLog] = []     # Actions of the tracer agent (AI mode).
    added_documents: list[SourceDocument] = []  # Pages the tracer opened (not in 02_search.json).


# ---------------------------------------------------------------------------
# STAGE 4 output: answers to the six core questions
# ---------------------------------------------------------------------------

class QuestionAnswer(BaseModel):
    question_id: str                 # "Q1".."Q6"
    question_text: str
    response: str                    # Template b "Response" column, careful wording.
    risk_call: RiskCall              # Template b "Potential risk?" column (a PROPOSAL).
    rationale: str                   # Why, in terms of the guide 1.4 rule.
    evidence_ids: list[str] = []
    staff_questions: list[str] = []  # What a human must decide.
    judged_by: str
    # "Attack from two sides": the case for each answer, and the reason each rejected answer was eliminated.
    case_for_risk: str = ""          # What points towards "yes".
    case_against_risk: str = ""      # What points towards "no".
    why_not_yes: str = ""            # The disqualifier that rules out "yes" (empty if the call is yes).
    why_not_no: str = ""             # The disqualifier that rules out "no" (empty if the call is no).
    # Debate between the judge and the challenger agent (AI mode only).
    challenge: str = ""              # The challenger's argument against the judge's first call.
    challenger_call: str = ""        # The call the challenger argued for.
    rebuttal: str = ""               # The judge's reply.
    call_before_challenge: str = ""  # The judge's first call, if the challenge changed it.


class QuestionAnswers(StageHeader):
    answers: list[QuestionAnswer]
    policy_questions: list[str] = []  # Questions about the manual itself (e.g. contradictions).


# ---------------------------------------------------------------------------
# STAGE 5 output: score and suggested route
# ---------------------------------------------------------------------------

class ScoreResult(StageHeader):
    risk_answers_count: int          # Number of RiskCall.YES (template c section 1).
    unclear_count: int
    risk_level: RiskLevel            # From the 0 / 1-2 / 3+ rule.
    risk_level_if_unclear_were_yes: RiskLevel  # Sensitivity: worst case if every 'unclear' is a risk.
    suggested_next_step: str
    suggested_decision_maker: str
    escalation_note: str
    verification_note: str
    disclaimer: str = (
        "Suggested by rule from guides 1.4 and 3.2. A person makes every decision."
    )


# ---------------------------------------------------------------------------
# STAGE 6 output: the assessment file manifest
# ---------------------------------------------------------------------------

class AssessmentManifest(StageHeader):
    files: dict[str, str]            # Logical name -> relative path, e.g. {"assessment_html": "06_assessment.html"}
    file_provenance: dict[str, Provenance] = {}  # Logical name -> who prepared that file.
    gaps: list[str]                  # Coverage gaps: what could not be checked.
