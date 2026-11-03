# Answer keys

One file per case, named `<case_id>.json`, written by the team BEFORE running the tool.
`scripts/evaluate.py` and `tests/test_end_to_end.py` compare the tool's results with these files.

Fields (the `//` notes are explanations only; real files are plain JSON):

```
{
  "expected_risk_calls": {"Q1": "no", "Q2": "no", "Q3": "yes", "Q4": "yes", "Q5": "unclear", "Q6": "no"},
  "expected_risk_level": "medium",
  "expected_risk_level_if_unclear_were_yes": "high",   // optional
  "expected_rejected_doc_ids": [],                      // sources that should be set aside (e.g. namesakes)
  "must_have_status": {"B3": "finding", "B5": "allegation"}
}
```
