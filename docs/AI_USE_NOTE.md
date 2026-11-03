# AI use note

*Fixed policy document: the same for every organisation assessed. Required by the Global Ethical Standards and
Policy (section 3.4): AI used in research must have documented guidance on data protection and reliability.*

## What the AI does and never does

| Agent | Does | Never does |
|---|---|---|
| Researcher | Decides what to search and read; follows leads to primary sources | Opens pages it was not shown; researches private individuals |
| Analyst | Labels each source (allegation, investigation, finding…) with an exact quote and the event's date | Upgrades an allegation to a fact |
| Tracer | Traces serious open issues back to their origin and forward to their outcome | Guesses an outcome it did not find |
| Judge | Drafts the six template b answers, with both sides | Treats an empty search as "no risk" |
| Challenger | Argues against each answer; the judge must reply | Has the final word: a person does |
| *(Code)* | Scores with the manual's fixed rule; routes | *No AI decides the risk level or the partnership* |

## How reliability is ensured

| Control | How |
|---|---|
| Traceability | Every finding has a source link and an exact quote, checked word for word |
| Limits of each source | Each row says what is safe and not safe to infer |
| No invented sources | The agent can only open links that appeared on a page |
| Identity | Similar names marked "not confirmed"; namesakes set aside |
| Second opinion | A challenger agent tests every answer; the debate is shown |
| Consistency | Fixed searches and scoring rule; rule-based baseline; 33 answer-key checks; 48 automated tests; repeat-run consistency measured |
| Transparency | Every file says who prepared it: partner, third party, staff or AI |
| Human review | Named reviewer, conflict-of-interest declaration, owns every decision |

## Data protection

| | |
|---|---|
| Inputs | Organisation name and relationship details only; no personal data |
| Sources | Public web pages and documents only; robots.txt obeyed; no logins or paywall bypass |
| AI provider | Public page text and the case card go to the chosen provider (Anthropic or OpenAI) under its API terms; cached so nothing is sent twice |
| Storage | Locally in `results/`; in production, WaterAid systems with named-contact access (guide 1.6) |

## For WaterAid to confirm

- Trusted source list and reliability ratings (`config/sources.json`, `criteria.json`)
- Which LLM provider, if any, is acceptable
- The two policy contradictions (fossil fuels; alcohol)
