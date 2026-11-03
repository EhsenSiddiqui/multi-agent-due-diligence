"""
The six pipeline stages, in order. Each module exposes `run(cfg, case_id)`.

Stages communicate ONLY through files in results/<case_id>/ (see io_utils.STAGE_FILES),
validated against schemas.py. That is why any stage can be rewritten, or re-run on
its own, without touching the others.
"""

from . import s1_intake, s2_search, s3_extract, s4_judge, s5_score, s6_outputs

# (number, name, module) in run order.
ORDER = [
    (1, "intake", s1_intake),
    (2, "search", s2_search),
    (3, "extract", s3_extract),
    (4, "judge", s4_judge),
    (5, "score", s5_score),
    (6, "outputs", s6_outputs),
]
