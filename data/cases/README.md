# Cases

Two kinds of case live here:

| Kind | Cases | Folder contains | Pages come from |
|---|---|---|---|
| **Real organisations** | `case_d_barrick`, `case_e_lululemon` | `case.json` only | The **live web** (Tavily or DuckDuckGo) |
| **Fictional test cases** | `case_a_brightwell`, `case_b_northstream`, `case_c_corvane` | `case.json` + `web/` | An offline test web, so results can be checked against answer keys |

**To add a real organisation:** make a folder `case_<letter>_<name>/` with a `case.json` like
`case_d_barrick/case.json` (name, `search_name`, aliases and subsidiaries, own websites, relationship
details, `"fictional": false`). No `web/` folder: the tool then searches the live web.

## Fictional test cases

Every fictional case is labelled so. Each case has a case card plus a small
**simulated web**: realistic HTML pages and PDFs, as the agent would meet them online.

```
data/cases/
  case_c_corvane/
    case.json                      <- the case card (what staff type in)
    web/
      index.json                   <- every URL in this case's simulated web
      corvane-group.example/who-we-are.html
      oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf
      ...
```

The folder name must equal `case_id` in `case.json`. Add an answer key with the same name
in `data/answer_keys/` so `scripts/evaluate.py` can check the tool.

## How the files are made

The pages and PDFs are **generated** by `scripts/build_demo_web.py`. Edit the stories in that
script and run:

```
python scripts/build_demo_web.py
```

This rebuilds every `web/` folder. The pages look like real sites (menus, cookie banners, ads,
"related stories" with misleading headlines, footers), and the PDFs have letterheads, reference
tables, running footers and a wrong metadata date. That gives the scraper realistic clutter to cope with.

## index.json

```json
{"pages": [
  {"id": "C3", "url": "https://oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf",
   "file": "oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf",
   "content_type": "application/pdf", "indexed": false, "origin": "third_party_public_source"}
]}
```

- `indexed: true`: a search engine can find this page.
- `origin`: who published the page: `partner_organisation` (on a domain listed in the case card's
  `websites`) or `third_party_public_source`. The same label is written into each file: an HTML comment
  at the top of a page, or the "Subject" property of a PDF.
- `indexed: false`: **only reachable by following a link** from another page. This is how the
  demo shows the agent at work. For example, the regulator's PDF notice is linked from an NGO
  post, but a search does not surface it.

## case.json

```json
{
  "case_id": "case_c_corvane",
  "organisation_name": "Corvane Group Holdings Ltd.",
  "aliases": ["Corvane Group", "Corvane", "Corvane Minerals S.A."],
  "relationship_type": "corporate_donor",
  "annual_value_usd": 250000,
  "presence": "multinational",
  "brand_affiliation": true,
  "declared_sectors": ["engineering services"],
  "countries_of_operation": ["Canada", "Chile"],
  "websites": ["corvane-group.example"],
  "notes": "FICTIONAL. ...",
  "fictional": true
}
```

- `relationship_type`: a key from `entry_criteria` in `config/criteria.json`.
- `websites`: the organisation's own domains. Pages on them are typed as its website,
  statements or annual reports.
- Third-party domains (news, regulators, NGOs) are typed in `config/sources.json`.

## The three cases provided

| Case | Story | What it tests |
|---|---|---|
| `case_a_brightwell` | Local office-supply firm, nothing adverse | "Low" result that still lists its gaps; harmless "workers"/"risk" hits; misleading "related stories" that must be stripped |
| `case_b_northstream` | 2019 safety fine, court bulletin PDF linked from the news story, factories in Bangladesh and Vietnam, weak NGO wage claim | Findings vs responses vs allegations; reaching the court PDF; countries missing from the case card |
| `case_c_corvane` | Disputed sale of a mining unit; regulator PDF only reachable by link; buyer's investor update contradicts the company; NGO campaign; unrelated "Corvane Dental" | The agent's follow-up research; "unclear" answers; rejecting a namesake |

## Making more cases

See `PROMPT_FOR_NEW_CASES.md`.
