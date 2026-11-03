"""Entity matching, keyword matching and sentence splitting."""

from a5dd.schemas import CaseCard, EntityMatch
from a5dd.text_utils import contains_any, match_entity, split_sentences

CORVANE = CaseCard(
    case_id="t", organisation_name="Corvane Group Holdings Ltd.",
    aliases=["Corvane Group", "Corvane", "Corvane Minerals S.A."], relationship_type="corporate_donor",
)


def test_exact_and_alias():
    assert match_entity("Corvane Group Holdings Ltd. announced", CORVANE)[0] == EntityMatch.EXACT
    assert match_entity("Corvane Group said today", CORVANE)[0] == EntityMatch.ALIAS
    assert match_entity("donors should cut ties with Corvane until tested", CORVANE)[0] == EntityMatch.ALIAS


def test_similar_name_is_uncertain_not_a_match():
    match, name = match_entity("OEFA investigates Corvane Minerales S.A.C. over discharge", CORVANE)
    assert match == EntityMatch.UNCERTAIN
    assert "Minerales" in name


def test_namesake_is_rejected():
    assert match_entity("Corvane Dental Clinic in Halifax was fined", CORVANE)[0] == EntityMatch.NONE


def test_keywords_match_word_starts_only():
    assert contains_any("water was polluted", ["pollut"])
    assert not contains_any("Northstream Apparel", ["stream"])  # 'stream' inside a name is not a topic


def test_splitter_keeps_company_abbreviations():
    s = split_sentences("Corvane Minerals S.A. has not been part of the group. It was sold.")
    assert s[0] == "Corvane Minerals S.A. has not been part of the group."
    assert len(s) == 2
