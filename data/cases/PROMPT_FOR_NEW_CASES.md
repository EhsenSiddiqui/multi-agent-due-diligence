# Making a new fictional case (with ChatGPT or any LLM)

New cases are added as one Python function in `scripts/build_demo_web.py`, using the
helpers already there (`news_page`, `corporate_page`, `ngo_page`, and PDF specs). Look at
`case_c()` in that file as the model.

## Steps

1. Paste the prompt below into ChatGPT, filling in the scenario.
2. Paste the function it returns into `scripts/build_demo_web.py`, and add a
   `write_case("case_d_yourname", case_d)` line at the bottom.
3. Create `data/cases/case_d_yourname/case.json` (see `README.md` in this folder).
4. Make it messier by hand: a vague headline, a date that does not match, a slightly different
   subsidiary name, an allegation the company denies.
5. Write the answer key `data/answer_keys/case_d_yourname.json` BEFORE running the tool.
6. Run `python scripts/build_demo_web.py`, then `python scripts/evaluate.py --run`.

---

## Prompt

You are writing FICTIONAL test data for a charity's partner due-diligence tool. Invent a company
that does not exist. Use only invented people, outlets and `.example` web addresses.

Write a Python function `case_d()` that returns `(pages, pdfs)`, using these helpers:

```python
news_page(site, section, title, author, date_iso, date_text, paragraphs, links=None, related=None, meta_date=True)
corporate_page(company, title, paragraphs, links=None, updated_text="", date_iso="")
ngo_page(org, title, date_iso, date_text, paragraphs, links=None)
```

- `pages` is a list of `(doc_id, url, indexed, html)` where html comes from one of the helpers.
- `pdfs` is a list of `(doc_id, url, indexed, spec)` where spec is a dict with keys:
  `letterhead, sub, title, ref (list of [label, value] rows, including ["Date", "12 August 2025"]), paras, footer, pdf_title`.
- In a paragraph, write `[[anchor text]]` and pass `links={"anchor text": "https://..."}` to make a hyperlink.
- `indexed=False` means only reachable by following a link: use it for at least one primary
  source (a regulator notice, court record or filing) linked from a news story or NGO post.
- `related=[...]` headlines appear in a sidebar that the scraper must ignore: include one with an
  alarming word (e.g. "fraud") that is NOT about our company.

Scenario: **[DESCRIBE IT, e.g. "a beverage company with an old water-pollution fine that was
overturned on appeal, plus a recent social-media campaign"]**

Requirements:
- 5 to 9 documents in total. Plain news style, 2 to 4 sentences per paragraph list.
- Include one company statement responding to an issue.
- Include one allegation clearly worded as a claim ("alleges", "calls on").
- Include one harmless page that still contains a word like "risk" or "workers".
- Do not label documents with their status; write them as a journalist or regulator would.

Return only the Python function.
