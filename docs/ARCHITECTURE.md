# Architecture

## The flow

```
data/cases/<case>/case.json
        │
        ▼
 [1] s1_intake ──────────► results/<case>/01_intake.json     (IntakeResult)
        │
        ▼
 [2] s2_search ──uses──► SearchProvider: simweb (test cases) | tavily / duckduckgo (live web)
        │                     └─► parsing.py (HTML/PDF → clean text, date, publisher, links)
        │        Part A: 33 fixed guide 1.3 searches (code)
        │        Part B: AGENT 1 Researcher: tool loop (web_search, open_page, finish) through
        │                ResearchSession, which enforces the budget and "only shown URLs"
        │                (rule mode: HeuristicLeadFinder proposes rounds of follow-ups)
        │        ───────► 02_search.json                     (SearchResults + agent log + summary)
        ▼
 [3] s3_extract ─uses──► AGENT 2 Analyst (llm, in parallel; long docs → retrieval.focus_text) | rules
        │        quality gate: no mention / namesakes out, quotes must be word-for-word
        │        issues.py: group into issues, timeline by event date, status state machine (code)
        │        AGENT 5 Tracer (llm): for serious open issues, back to origin / forward to outcome;
        │                new pages → analyst → issues rebuilt
        │        ───────► 03_evidence.json                   (EvidenceTable + issues + trace log)
        ▼
 [4] s4_judge ───uses──► AGENT 3 Judge ⇄ AGENT 4 Challenger (llm) | rules
        │        challenger objects → judge holds (rebuttal) or changes its call; both saved
        │        ───────► 04_answers.json                    (QuestionAnswers + debate)
        ▼
 [5] s5_score    plain code, guide 1.4 counting rule
        │        ───────► 05_score.json                      (ScoreResult)
        ▼
 [6] s6_outputs  ──uses──► reports.py
                 ───────► 06_assessment.html, 06_assessment.pdf, 06_evidence.csv,
                          06_template_b/c/e_risks.docx, 06_manifest.json
        │
        ▼
 app/review_app.py (person reviews, overrides, saves 07_review.json)
```

## The three rules that keep files independent

1. **Stages talk only through files.** No stage imports another stage. Each reads the JSON files that
   earlier stages wrote, through `io_utils.read_stage`, which validates them against `schemas.py`.
2. **Swappable work sits behind an interface.** Search, extraction and judging each have an abstract
   base class (`providers/base.py`). Stages ask the factory for "the search provider" and never name
   an implementation. Settings choose which one runs.
3. **The contract is the promise.** Any implementation, current or future, must return the models in
   `schemas.py`. Downstream code relies on the contract, not on how it was produced.

This is tested: `tests/test_contracts.py` plugs in a brand-new search provider and a deliberately
broken extractor, and checks that the rest of the pipeline works, and that the quality gate still
catches invented quotes.

## Where the AI is, and where it deliberately is not

| Step | AI mode (default) | Baseline (`--rules`) | Why |
|---|---|---|---|
| 1 Intake | Rules | Rules | Thresholds are fixed policy |
| 2 Fixed searches | Code | Code | Consistent, auditable coverage (guide 1.3) |
| 2 Follow-up research | **Researcher agent** (tool loop) | Rule-based planner | Deciding where to look needs judgement |
| 3 Reading sources | **Analyst agent** (+ passage retrieval for long documents) | Keyword rules | Reading comprehension |
| 3 Issue timelines | Code | Code | Same evidence, same timeline and status |
| 3 Closing open ends | **Tracer agent** | None | Following a story to its end needs judgement |
| 4 Answering Q1-Q6 | **Judge agent ⇄ Challenger agent** | Rules | Weighing evidence; a second agent checks the first |
| 5 Score | Rules | Rules | **Never AI**: same answers, same score |
| 6 Outputs | Templates | Templates | Formatting only |
| Review | Person | Person | Policy: a human decides |

Why a fixed pipeline with agents inside it, rather than free-roaming agents negotiating: due diligence needs
the same coverage every time and an audit trail. Autonomy is used where it adds value (where to look, how to
read, testing a call) and code holds everything that must not vary.

Safety rails stay in code whichever implementation runs: entity matching, the quote check,
dropping invented evidence IDs and invented URLs, the agent's budget, and scoring.

## How to change things safely

| You want to... | Change | Do not touch |
|---|---|---|
| Add a real organisation | New `data/cases/<case>/case.json` (no `web/` folder) | Code |
| Switch AI provider or models | `settings.yaml: llm` | Any stage |
| Turn one agent off | e.g. `settings.yaml: challenger: none` | Any stage |
| Improve HTML/PDF scraping | `parsing.py` only | Any stage or provider |
| Try a new search API | New class in `providers/`, one registry line | Any stage |
| Change a rule (e.g. threshold) | `config/criteria.json` | Code |
| Answer the fossil-fuels question | `settings.yaml: policy_switches` | Code |
| Add a field to the evidence | Add it as optional in `schemas.py` | Readers keep working |
| Rename or remove a field | `schemas.py` + bump `SCHEMA_VERSION` + update readers | (breaking change) |
