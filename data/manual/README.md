# Put the zip contents here

Unzip `A5_Due_Diligence.zip` into this folder, so it looks like:

```
data/manual/
  0.0 Water Care Global Ethical Standards and Policy.pdf
  1.1 Water Care Ethical Check Criteria.pdf
  1.3 Water Care Conducting Desk-Based Research.pdf
  1.4 Water Care Risk Scoring Guidance.pdf
  ...
  a. Water Care Ethical due diligence questions template.docx
  b. Water Care Ethical due diligence questions template - desk based.docx
  c. Water Care Risk scoring, verification, and low risk decision record template.docx
  e. Water Care EC decision making template.docx
```

## Does the code read these files?

**Not in the demo.** The rules from the manual have already been turned into data in
`config/criteria.json`, and every value there cites the manual file it came from.
Keep the PDFs here so anyone can check that file against the source.

## Which files the rules came from

| criteria.json section | Manual file |
|---|---|
| `standards` | 0.0, section 2 |
| `questions`, `lookback_years` | template b; guide 1.4 for each `risk_rule` |
| `search_terms` | template b, "Key search terms" |
| `sectors` | 0.0 s3.3 and 4.1 (excluded); 1.1 (high-risk) |
| `entry_criteria` | 1.1 (simplified) |
| `routing` | 1.4 and 3.2 |

## Better version later

`s6_outputs.py` could open templates b, c and e from this folder and fill their table
cells directly, so staff get their exact forms instead of look-alike documents.
