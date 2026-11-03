# Review workflow, uncertainty and cost

*Fixed process document: the same for every organisation assessed. The cost figures come from the three test cases
(rule-based baseline); `python scripts/effort_estimate.py` adds any case you have run, including real organisations.*

## 1. Review workflow

**Staff enter case card → agent researches (minutes) → reviewer checks the report → routed by risk level**

| Step | Reviewer does |
|---|---|
| 1 | Declares no conflict of interest (guide 1.5). The review screen will not save without it |
| 2 | Reads *At a glance*, *Risks indicated* and *Needs your judgement* |
| 3 | Spot-checks the sources behind each yes or unclear answer |
| 4 | Reads *Limits*; decides whether more research is needed |
| 5 | Accepts or changes each call; saved as a staff record (`07_review.json`) |

| Level | Next step | Who decides | Form |
|---|---|---|---|
| Low | Sign off | Team lead, manager or director | Template c §3 |
| Medium | Full ethical check (guide 2.1) | Function or Regional Director | Template e |
| High | Drop, or full ethical check | Function or Regional Director; may escalate | Template e |

**Escalate** (guide 3.2) if high-risk, complex or contentious: Director → Chief Executive → Global Executive → Federation Board.

## 2. Uncertainty

| Situation | What the tool does |
|---|---|
| Evidence thin or conflicting | Answers **unclear** and states the question that would settle it |
| Making any call | Judge builds the case for and against; must rule out the other answers with a reason |
| One reading could be wrong | A **challenger agent** argues the other way; the judge must reply; both shown |
| Each source | States what is **safe** and **not safe** to infer |
| Nothing found | Says "in the sources searched", never "no risk" |
| Issue with no known ending | Tracer agent searches for the outcome; if none is found, says so and asks staff |
| Old or repeated news | Timelines by event date; current status of each issue; independent sources counted once |
| How much rides on open questions | Shows a **worst case**: the level if every unclear were yes |
| Searches fail | Some: red warning. All: run stops |

## 3. Cost

| Case | Manual (guide 1.3) | Answers to review | Sources to check | Review with tool | Saved |
|---|---|---|---|---|---|
| Brightwell (clean) | 60–120 min | 0 | 0 | ~6 min | 54–114 min |
| Northstream (medium) | 60–120 min | 3 | 5 | ~31 min | 29–89 min |
| Corvane (ambiguous) | 60–120 min | 6 | 8 | ~46 min | 14–74 min |

| | |
|---|---|
| How review time is modelled | Report words ÷ 200 per min + 5 min per source to check + 5 min overhead |
| Running cost (AI, live web) | ~45 searches and ~40–90 AI calls per organisation, mostly on the cheaper analyst model; add prices in `config/settings.yaml` |
| Maintenance | Keep `sources.json` and `criteria.json` current; re-run tests after any change |
| To prove real benefit | Pilot on 20–30 past cases: issues missed, false alarms, reviewer agreement, real time |
