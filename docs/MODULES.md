# What each file does, what was simplified, and what a better version looks like

Read [ARCHITECTURE.md](ARCHITECTURE.md) first for how the pieces connect.

Legend: **AI** = an AI agent (default mode). **Baseline** = the rule-based version (`--rules`).
**Better** = how to improve it later.
Every "Better" version can be built without changing any other file, as long as it keeps
producing the same data contract (see "Output").

---

## Core files (`src/a5dd/`)

### `schemas.py`: the data contracts
- **What it does:** defines, with Pydantic, every object passed between stages:
  `CaseCard`, `IntakeResult`, `SearchResults`, `EvidenceTable`, `QuestionAnswers`, `ScoreResult`,
  `AssessmentManifest`, plus the fixed vocabularies (`EvidenceStatus`, `RiskCall`, ...).
- **Why it matters:** this is the only file every stage depends on. Validation means a malformed file
  fails loudly at the next stage instead of producing a quietly wrong report.
- **Changing it:** adding a new *optional* field is safe. Renaming or removing a field, or changing what a
  value means, is a breaking change: bump `SCHEMA_VERSION` and update the readers.

### `config.py`: loads settings and rules
- **What it does:** reads `config/settings.yaml` and `config/criteria.json` into one `Config` object, with
  helpers such as `cfg.provider("search")`, `cfg.switch("fossil_fuels_excluded")` and `cfg.active_sectors()`.
- **Simplified:** one settings file, no per-user or per-country overrides.
- **Better:** per-country-programme rule overrides (the policy allows local risk appetites), and a
  validation schema for criteria.json itself.

### `io_utils.py`: reads and writes stage files
- **What it does:** `write_stage` saves a stage's output as `results/<case_id>/0N_name.json`; `read_stage`
  loads it, checks `schema_version`, and validates it against the expected model.
- **Better:** store results in a database or object storage with the same read/write functions, so
  stages do not notice the change.

### `text_utils.py`: shared text rules
- **What it does:** `match_entity` (is this document about our organisation, an alias, a similar-named
  entity, or a namesake?), `contains_any` (keyword matching at word starts only), `split_sentences`,
  `best_quote`.
- **Simplified:** names are compared as strings; "Corvane Minerales" vs "Corvane Minerals S.A." is
  judged by text similarity.
- **Better:** resolve entities through a company registry (e.g. OpenCorporates) using registration
  numbers, and store ownership as a graph so parent and subsidiary links are explicit.

### `parsing.py`: the scraper (unstructured → structured)
- **What it does:** turns raw HTML or PDF bytes into a clean `SourceDocument`.
  HTML: removes scripts, menus, cookie banners, ads, newsletter boxes, "related stories", comments; keeps
  the article; collects in-article links. PDF: extracts text with pypdf, drops running headers/footers (by
  position on the page) and page numbers, re-joins wrapped lines. Both: find title, publication date
  (metadata, `<time>` tag, visible byline, or first date in the text; a PDF's metadata date is ignored)
  and publisher, and record how each was found in `parse_notes`. Then types the source using the case
  card's `websites`, `config/sources.json`, or fallback rules.
- **Simplified:** boilerplate removal by tag and class keywords; no OCR; English only.
- **Better:** trafilatura or readability for article extraction across thousands of layouts; OCR
  (Tesseract) for scanned PDFs; language detection and translation.

### `inference.py`: "safe to infer" rules
- **What it does:** for every evidence row, writes what the source is safe to infer and what it is not
  (allegation ≠ fact, investigation ≠ finding, statement ≠ truth, similar name ≠ same company,
  self-description ≠ verified, announced sale ≠ completed sale). Applied in stage 3 to every extractor's output.
- **Simplified:** fixed templates per status.  **Better:** source-specific limits drafted by an LLM, checked against these rules.

### `retrieval.py`: passage retrieval (on-the-fly RAG index)
- **What it does:** splits documents into passages (exact slices of the text, so quotes still verify) and ranks
  them with BM25. `focus_text` cuts a long document down to its opening plus its most relevant passages for the
  analyst; `PassageIndex.search` powers the agents' `search_found_pages` tool.
- **Simplified:** keywords only.  **Better:** add embeddings and a reranker (hybrid retrieval).

### `issues.py`: issue timelines
- **What it does:** groups evidence into issues (same label words or a shared proper name), orders events by when
  they happened, sets the current status with a small state machine (only moves forward; an appeal reopens an
  outcome), records who reported it first and how many independent sources there are, and marks serious issues.
- **Simplified:** grouping by label similarity.  **Better:** event coreference with embeddings, confirmed by an LLM.

### `research_tools.py`: the agents' toolset
- **What it does:** `ResearchSession`, shared by the researcher and the tracer: web search (optionally recent
  only), open page (only URLs shown), search inside found pages, and an overview that includes results listed but
  not opened. Budgets and logging are enforced here.

### `reports.py`: the HTML and PDF report
- **What it does:** `render_html` builds one calm, self-contained HTML page ordered by the call, not by an
  invented urgency score: at a glance → risks indicated ("yes", with key evidence inline) → needs your judgement
  ("unclear", each with the question that would settle it) → no issues found ("no", one short list) → evidence,
  most serious first (click for quote, safe / not safe to infer) → limits; logs, provenance and audit trail
  folded away. Within each section, cards are ordered by their strongest evidence (finding > investigation >
  allegation). Every card keeps its Q-number so it maps back to template b.
  Each answer card shows the judge–challenger debate when there was one. The evidence section lists the
  sources the answers cite; other sources reviewed are folded away. `write_pdf` builds the same in A4,
  citing only the evidence the answers use (the CSV has everything). Used only by stage 6.
- **Better:** WaterAid branding; a signed, version-stamped PDF for the register.

### `pipeline.py`: command-line runner
- **What it does:** runs stages 1-6 for one case (`--case`) or all (`--all`). Can start from a later stage
  (`--from 4`), run one stage (`--only 3`), use the rule-based baseline (`--rules`), or switch the follow-up
  research off (`--no-agent`). Stops early with a clear message if an AI agent is on but no API key is set;
  with `--all`, a failed case does not stop the others.
- **Better:** a job queue so many assessments can run at once, plus run IDs so earlier results are kept.

---

## Stages (`src/a5dd/stages/`)

Each stage has a `run(cfg, case_id)` function, reads the earlier stages' files and writes exactly one file.

### `s1_intake.py`: Step 1, intake
- **Input:** `data/cases/<case_id>/case.json` · **Output:** `01_intake.json` (`IntakeResult`)
- **What it does:** applies the guide 1.1 thresholds (does this relationship need an ethical check?) and
  flags excluded or high-risk sectors staff already declared.
- **Simplified:** only common partner types; guide 1.1 special cases (legacies, governments, consortia) are left out.
- **Better:** a web form for staff, and all of guide 1.1 encoded with a unit test per row.

### `s2_search.py`: Step 2, research
- **Input:** `01_intake.json` · **Output:** `02_search.json` (`SearchResults`)
- **Part A:** the fixed guide 1.3 protocol: one profile query plus "<search name> + term" for each of the 32
  template b terms. The same every time, so coverage is consistent and auditable.
- **Part B, AI:** hands the **Researcher agent** a `ResearchSession`: its tools (search, open page) with the
  guard rails built in (only URLs it was shown, search/page budgets). The agent's summary and open questions
  are saved; open questions become the first items in the report's Limits.
- **Part B, baseline:** rounds of rule-based follow-ups until nothing new turns up.
- Pages come from the offline test web for the fictional cases, or the live web for real organisations
  (`providers.search: auto`). Every action is logged with its result and reason.
- **Better:** a cost budget as well as an action budget; registries and sanctions lists as extra tools.

### `s3_extract.py`: Step 3, analyse, build issue timelines, trace open ends
- **Input:** `01_intake.json`, `02_search.json` · **Output:** `03_evidence.json` (`EvidenceTable`)
- **What it does:** skips sources that never mention the organisation, asks the **Analyst agent** (several
  sources at once) or the keyword rules to turn each document into evidence items, then applies the same quality
  gate to every item: namesakes are rejected; a quote that is not word for word in the source is rejected;
  unknown question IDs are removed. Rejected items are kept, with the reason. Then builds issue timelines
  (`issues.py`) and, in AI mode, sends the **Tracer agent** after the most serious open issues; pages it opens are
  analysed the same way and the issues are rebuilt.
- **Better:** add a second check on the status label (e.g. a second model, or a rule that a "finding"
  needs words like fined or convicted in the quote).

### `s4_judge.py`: Step 4, judge each question
- **Input:** `01_intake.json`, `03_evidence.json` · **Output:** `04_answers.json` (`QuestionAnswers`)
- **What it does:** asks the **Judge agent** (or rules) to answer Q1-Q6. In AI mode the **Challenger agent** then
  tests each answer; if it objects, the judge must reply (hold with a rebuttal, or change the call). Adds
  "policy questions" (the manual's own contradictions, with the setting used for this run).
- **Better:** also show where the AI judge and the rule-based judge disagree, as a third opinion.

### `s5_score.py`: Step 5, score and route
- **Input:** `04_answers.json` · **Output:** `05_score.json` (`ScoreResult`)
- **What it does:** counts "yes" answers and applies the guide 1.4 rule (0 = low, 1-2 = medium, 3+ = high),
  reports the worst case if every "unclear" were "yes", and suggests the next step and decision maker
  (guides 1.4 and 3.2).
- **Deliberately not an LLM:** the same answers must always give the same score.
- **Better:** cross-federation routing (guide 3.2 has a second column for multi-country relationships).

### `s6_outputs.py`: Step 6, write the assessment file
- **Input:** all earlier files · **Output:** `06_assessment.html`, `06_assessment.pdf`, `06_evidence.csv`,
  `06_template_b.docx`, `06_template_c.docx`, `06_template_e_risks.docx`, `06_manifest.json`
- **What it does:** builds the gaps list, writes the assessment file as HTML (for reviewers) and PDF (for the
  record) via `reports.py`, and fills the tool's parts of templates b, c and e. Human-only boxes stay blank.
- **Simplified:** the Word files are new documents that copy the templates' headings; they do not edit the originals.
- **Better:** open the original templates from `data/manual/` and fill their table cells.

---

## Swappable providers (`src/a5dd/providers/`)

### `base.py`: the interfaces
- `SearchProvider.search(query, case, n)`, `.find(text, case, n)` (result list only) and `.fetch(url, case)`
- `ToolResearcher.research(case, tools)` (AI) / `LeadFinder.propose(...)` (baseline)
- `EvidenceExtractor.extract(doc, case)`, `QuestionJudge.judge(...)` / `.reconsider(...)`, `Challenger.challenge(...)`

### `__init__.py`: the factory
Maps names in `settings.yaml` to classes, with lazy imports so the baseline never needs the AI or web packages.
`providers.search: auto` picks the offline test web when a case ships one, otherwise the live web.

| File | Role | Mode | What it does |
|---|---|---|---|
| `llm_client.py` | Shared | AI | Talks to Claude or OpenAI: `complete_json` (one answer) and `run_tools` (a real tool-use loop). One model per agent role; every response cached |
| `leads_llm.py` | **Agent 1: Researcher** | AI | Tool loop with `web_search` (optionally recent), `open_page`, `search_found_pages`, `finish`; sees results listed but not opened; prompt tells it to find gaps, primary sources, outcomes of allegations, subsidiaries, water issues |
| `extractor_llm.py` | **Agent 2: Analyst** | AI | Lists each issue in one source with status, exact quote, questions, topics; if code cannot confirm the name but the analyst says it is about the organisation, the item is kept as "not confirmed" |
| `judge_llm.py` | **Agent 4: Judge** | AI | Answers one question from both sides; `reconsider` replies to the challenger |
| `tracer_llm.py` | **Agent 3: Tracer** | AI | For serious issues with an open end: back to the original record, forward to the outcome; reports "no outcome found" rather than guessing |
| `challenger_llm.py` | **Agent 5: Challenger** | AI | Looks for allegations treated as facts, wrong companies, old evidence, "no" resting on nothing, ignored evidence |
| `search_live.py` | Live web | AI or baseline | Tavily or DuckDuckGo search; `WebFetcher` downloads pages (robots.txt, User-Agent, 10 MB cap, HTML/PDF only) and scrapes them with `parsing.py` |
| `search_simweb.py` | Offline test web | Both | Search engine over the fictional cases' pages; some pages are only reachable by link |
| `leads_heuristic.py` | Researcher | Baseline | Rules: follow links to primary sources; search the other party in an ownership change |
| `extractor_heuristic.py` | Analyst | Baseline | Keyword rules for status, topics and questions |
| `judge_heuristic.py` | Judge | Baseline | Guide 1.4 rules as code, attacking from two sides |

**Better:** a second model family as challenger (different blind spots); cost tracking per assessment;
company registries (e.g. OpenCorporates) and sanctions lists (OpenSanctions) as researcher tools.

---

## Other folders

| Path | What it is |
|---|---|
| `config/settings.yaml` | Which agent or rule runs each step; models; budgets; policy switches; paths |
| `config/criteria.json` | The manual's rules as data, each citing its source file |
| `data/manual/` | Put the unzipped A5 files here (reference) |
| `config/sources.json` | Known domains: publisher and source type (so reliability) |
| `data/cases/` | Real organisations (case card only) and fictional test cases (case card + offline web); see its README |
| `scripts/build_demo_web.py` | Generates the fictional web pages and PDFs (edit stories, then re-run) |
| `data/answer_keys/` | Expected results per case, written by the team |
| `results/<case_id>/` | One file per stage, plus the assessment file and Word templates |
| `scripts/evaluate.py` | Compares results with the answer keys |
| `scripts/effort_estimate.py` | Modelled review effort and running cost per case (assumptions in `settings.yaml` → `effort`) |
| `app/review_app.py` | Streamlit review screen; saves reviewer decisions to `07_review.json` |
| `tests/` | Unit and end-to-end tests (`pytest`) |
