# How the A5 due-diligence agents work, in plain words

## 1. The original problem

Water Care (the charity in the WaterAid challenge) takes money and partnerships from companies. Before it does,
a member of staff must check that the company will not harm the charity's mission or reputation. Today that
check is done by hand:

- search the company's name with **33 fixed search terms** ("bribery", "pollution", "investigation"...),
  reading the first pages of results for each;
- answer **six questions** on a form (template b), e.g. *Has it been investigated in the last 10 years?
  Does it harm communities' access to water?*;
- count the "yes" answers to get a risk level (0 = low, 1–2 = medium, 3+ = high) and send it to the right
  person to decide.

It takes **one to two hours per company**, and the result depends on who does it: one person opens the
regulator's PDF, another stops at the headlines.

## 2. What we are solving, and what we are not

| We do | We do not |
|---|---|
| Do the desk research: search, read, label, draft the six answers | Approve or reject a partner. A person decides |
| Show every claim with its source and an exact quote | Present allegations as facts |
| Say clearly what is unclear and what would settle it | Hide uncertainty behind a score |
| Fill the charity's own forms | Research private individuals |

## 3. What the system looks like

A fixed pipeline of seven steps. AI agents work **inside** steps 2–4, where judgement is needed. Plain code does
everything that must be the same every time. A person makes the decision.

```
 1 Intake        2 Research            3 Analyse + trace          4 Answer             5 Score   6 Report   7 Decide
 (code)          (code + Researcher)   (Analyst + Tracer + code)  (Judge ⇄ Challenger) (code)    (code)     (person)
```

## 4. The agents and their jobs

| Agent | Job | Tools | Kept in check by (code) |
|---|---|---|---|
| **Researcher** | After the 33 fixed searches, decides what else to search and read, and follows links to original records | web search (optionally last 12 months only), open page, search inside found pages | May only open links it was shown; budget of searches and pages |
| **Analyst** | Reads each source and lists each issue: allegation, investigation, finding, company response or background, with an exact quote, the event's date, and the issue it belongs to | One source at a time; long documents are cut down to their most relevant passages first | Every quote must appear word for word in the source; the code decides who a source is about |
| **Tracer** | For serious issues with an open end, traces **back** to where the story started and **forward** to how it ended | Same as the researcher | Same guard rails; "no outcome found" is reported, never guessed |
| **Judge** | Answers the six questions, building the case for and against each answer | The evidence and the issue timelines | Can only cite evidence that exists |
| **Challenger** | Argues for a different answer; the judge must reply (hold or change) | The same evidence | Both sides are shown to the reviewer |
| *Code* | Fixed searches, quote check, issue timelines, scoring, routing, report | | Never AI |

## 5. The hard parts, and how each is handled

| Challenge | What could go wrong | How the system handles it | Who |
|---|---|---|---|
| **Lots of praise, one problem** (imbalance) | Press releases repeated everywhere push one critical article off the first page | Searches use risk words, one search per term. Results listed but not opened are shown to the researcher, so a critical headline is not lost. The researcher is told to look where problems are recorded: regulators, courts, NGO trackers, local-language news. Scoring is a count of risks, not a vote, so praise cannot cancel a fine | Code, Researcher |
| **One story, many copies** (amplification) | Twenty articles repeating one NGO report look like twenty problems | Evidence is grouped into issues; the report shows the number of *independent* sources and who reported it first | Code, Tracer |
| **Time** | A 2025 article about a 2012 spill; an old allegation that was later dismissed | Each item records when the event happened, not just when it was published. Issues are shown as timelines, oldest first | Analyst, code |
| **How an issue developed** | A headline is read as the current state | A small state machine sets each issue's current status (reported → investigated → charged → ruled/fined/settled/dismissed → remediated). It only moves forward, except that an appeal reopens an outcome | Code |
| **Open ends** | "Alleged in 2019" with nothing after it | The tracer searches for the original record and for the outcome, including the last 12 months. If none is found, the report says so and adds a question for staff | Tracer |
| **Long documents** | A 200-page report: the problem is on page 80 | Passage retrieval: the document is split into passages and the ones relevant to the six questions are read, wherever they are | Code (retrieval), Analyst |
| **Made-up content** | An invented quote, link or source | Quotes checked word for word; links limited to ones shown; unknown evidence IDs dropped | Code |
| **One AI reading could be wrong** | The judge leans too far one way | The challenger argues the other side; the judge must answer | Challenger |
| **Wrong company** | "Corvane Minerales" vs "Corvane Minerals S.A."; a dental clinic with the same name | Similar names marked "not confirmed"; clear namesakes set aside | Code, Analyst |
| **Uncertainty** | Doubt turned into a confident answer | "Unclear" answers with the question that would settle them; what each source can and cannot prove; a worst-case risk level | Judge, code |
| **Searches fail** | Nothing found because nothing loaded | Some fail: red warning. All fail: the run stops and writes nothing | Code |

## 6. Where "RAG" fits

RAG (retrieval-augmented generation) means: **find the relevant text first, then let the AI answer only from it.**
The system uses it at two levels:

| | What is searched | Known in advance? |
|---|---|---|
| **Finding documents** (agentic RAG) | The live web | No. The agents decide what to look for and where |
| **Finding passages** | Every document found for this company, indexed on the spot | Yes, once downloaded |

The charity's policy manual is **not** used as a RAG source for the rules. The rules (thresholds, scoring) are
written as fixed data, so the same evidence always gives the same level.

## 7. How do we know it gives the right answer?

Yes, there is **ground truth**, as in classic machine learning, but at several levels, because an agent does
several jobs, not one:

| Level | Ground truth | Measure | In this project |
|---|---|---|---|
| **Final answer** | The correct call for each question and the risk level, written by people before running the tool | Accuracy, checks passed | Answer keys for 3 test companies: 33 checks |
| **Retrieval** | The sources the research must reach (incl. pages only reachable by following a link) | Recall: share found | `must_find_doc_ids` |
| **Labelling** | The right label for key sources (allegation, investigation, finding...) | Accuracy; with more labels, precision and recall per class | `must_have_status` |
| **Timelines** | The current status of key issues | Accuracy | `expected_issue_status` |
| **Faithfulness** | Every quote must be in its source | Checked automatically on every run | Quote gate |
| **Each agent's value** | Same cases, one agent switched off | Does anything get worse? (ablation) | `evaluate.py --ablate` |
| **Consistency** | AI answers vary between runs | Share of questions with the same call in every run | `evaluate.py --ablate --repeat 3` |
| **Process** | Did the agent take sensible steps? | Read the agent log; count wasted or failed actions | Every action is logged with its reason |
| **Real use** | Reviewers' decisions | How often reviewers change the tool's calls | `07_review.json` from the review screen |

Two things are different from classic ML:

1. **The output is not one number.** It is a chain (search → read → label → answer), so each link needs its own
   ground truth. A wrong final answer is traced back to the link that failed.
2. **The same input can give different outputs.** So you run it more than once and measure consistency, not
   just accuracy.

**What is missing today:** three test companies is a small test set, designed by the team. The next step is a
larger labelled set: 20–30 past assessments done by staff, plus 50–100 hand-labelled source snippets to measure
the analyst's precision and recall per label. Free-text quality (are the answers clear and fair?) can be scored
by a second model with a written rubric, checked against people's scores on a sample.

## 8. Commands

```
python -m a5dd.pipeline --case case_d_barrick        # a real company: live web + AI agents (API key)
python -m a5dd.pipeline --all --rules                # rule-based baseline, no AI
python scripts/evaluate.py --run                     # baseline vs answer keys
python scripts/evaluate.py --ablate --repeat 3       # each agent's value + consistency (API key)
```
