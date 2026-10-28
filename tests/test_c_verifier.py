"""C5: the answer verifier. Traces use the mini graph's numbers (tests/fixtures/README.md)."""

import json
import re

import pytest

from app.agent.verifier import NOTHING_VERIFIED, AnswerVerifier, TraceFacts, numbers_in
from app.models.agent import AgentAnswer, AgentClaim, ToolCall

PETROLIA, AEROLYS, AUTOROUTE, CUPRUM = "FRTEST000011", "FRTEST000029", "DETEST000060", "DETEST000078"

EXPOSED = ToolCall(
    tool="most_exposed",
    args={"factor_code": "BRENT", "min_r2": 0.1},
    result=[
        {"company_isin": PETROLIA, "company_name": "Petrolia SA", "factor_code": "BRENT",
         "relation": "SENSITIVE_TO", "truth": "estimate", "beta": 0.6213, "r2": 0.48},
        {"company_isin": AEROLYS, "company_name": "AeroLys", "factor_code": "BRENT",
         "relation": "SENSITIVE_TO", "truth": "estimate", "beta": -0.41, "r2": 0.3},
    ],
)  # fmt: skip
EXPOSURES = ToolCall(
    tool="company_exposures",
    args={"isin": PETROLIA},
    result=[
        {"company_isin": PETROLIA, "factor_code": "BRENT", "relation": "SENSITIVE_TO", "truth": "estimate",
         "beta": 0.6213, "r2": 0.48},
        {"company_isin": PETROLIA, "factor_code": "BRENT", "relation": "PRODUCES", "truth": "fact"},
    ],
)  # fmt: skip
SHOCK = ToolCall(
    tool="simulate_shock",
    args={"factor_code": "BRENT", "pct": 20},
    result={"lines": [{"isin": PETROLIA, "name": "Petrolia SA", "impact_pct": 12.4, "truth": "estimate"},
                      {"isin": AEROLYS, "name": "AeroLys", "impact_pct": -8.2, "truth": "estimate"}]},
)  # fmt: skip
CLAIMS = ToolCall(
    tool="hypotheses",
    args={"isin": AUTOROUTE},
    result=[{"claim_id": "c1", "subject_isin": CUPRUM, "object_isin": AUTOROUTE, "confidence": 0.8}],
)
PREDICTED = ToolCall(
    tool="predicted_exposures",
    args={"isin": AUTOROUTE},
    result=[{"company_isin": AUTOROUTE, "factor_code": "EURUSD", "relation": "PREDICTED_SENSITIVITY",
             "truth": "prediction", "prob": 0.71, "source": "fixture-v0"}],
)  # fmt: skip

KNOWN = {f"Company:{isin}" for isin in (PETROLIA, AEROLYS, AUTOROUTE, CUPRUM)} | {
    "Factor:BRENT",
    "Factor:EURUSD",
    "Claim:c1",
}


class KnownRefs:
    """Stand-in database: answers the existence queries from KNOWN and counts them."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def read(self, query: str, params: dict) -> list[dict]:
        """Rows {key} for the asked keys that exist under the query's label."""
        self.queries.append(query)
        label = re.match(r"MATCH \(n:(\w+)\)", query).group(1)
        return [{"key": key} for key in params["keys"] if f"{label}:{key}" in KNOWN]


def claim(text: str, truth: str, *evidence: str) -> AgentClaim:
    """Shorthand for a writer claim."""
    return AgentClaim(text=text, truth=truth, evidence=list(evidence))


def verify(*claims: AgentClaim, trace=(EXPOSED, EXPOSURES, SHOCK, CLAIMS, PREDICTED), text=None) -> AgentAnswer:
    """Run the verifier on an answer made of these claims (prose = the claims joined, unless given)."""
    answer = AgentAnswer(
        answer=text if text is not None else " ".join(item.text for item in claims),
        claims=list(claims),
        trace=list(trace),
    )
    return AnswerVerifier(KnownRefs()).verify(answer)


# --- supported claims stay -------------------------------------------------------------------------


def test_a_supported_claim_is_kept_unchanged():
    """Beta and r2 as the tool returned them (rounded): kept, prose untouched, no reason."""
    good = claim("Petrolia historically moved with Brent: beta 0.62, r2 48 %.", "estimate",
                 f"Company:{PETROLIA}", "Factor:BRENT")  # fmt: skip
    result = verify(good, text="Petrolia moved with Brent (beta 0.62).")
    assert result.claims == [good]
    assert result.removed_claims == []
    assert result.answer == "Petrolia moved with Brent (beta 0.62)."


@pytest.mark.parametrize(
    "text",
    [
        "A +20 % Brent shock gives Petrolia +12.4 %.",  # the shock size comes from the tool arguments
        "AeroLys falls 8.2 % in that scenario.",  # no sign written: the size is compared
        "AeroLys: −0.41 beta to Brent.",  # Unicode minus
        "Petrolia beta 0,62.",  # French decimal comma
        "Two companies have an oil beta above r2 0.1.",  # words are not numbers; 0.1 is an argument
    ],
)
def test_rounding_signs_and_formats_are_accepted(text):
    """What a writer may legitimately do with a number the tool returned."""
    result = verify(claim(text, "estimate", f"Company:{PETROLIA}", "Factor:BRENT"))
    assert len(result.claims) == 1, result.removed_claims


def test_codes_and_isins_are_not_numbers():
    """EUR10Y, US10Y and an ISIN contain digits but are not numbers to check."""
    assert numbers_in(f"EUR10Y and US10Y for {PETROLIA}") == []
    assert [str(number) for number in numbers_in("beta -0.41, impact 12.4 %")] == ["-0.41", "12.4%"]


# --- invented or unsupported claims go ----------------------------------------------------------------


def test_an_invented_number_is_removed_and_the_prose_rebuilt():
    """0.85 appears in no tool result: removed with its reason; the prose keeps only the verified claim."""
    good = claim("Petrolia's beta to Brent is 0.62.", "estimate", f"Company:{PETROLIA}", "Factor:BRENT")
    invented = claim("AeroLys has a beta of 0.85 to Brent.", "estimate", f"Company:{AEROLYS}", "Factor:BRENT")
    result = verify(good, invented, text="Petrolia 0.62 and AeroLys 0.85.")
    assert result.claims == [good]
    assert result.removed_claims[0].text == invented.text
    assert "0.85" in result.removed_claims[0].reason
    assert result.answer == good.text


def test_a_wrong_sign_is_an_unsupported_number():
    """AeroLys moves against Brent (-0.41): '+0.41' is not what the tool said."""
    result = verify(claim("AeroLys: beta +0.41 to Brent.", "estimate", f"Company:{AEROLYS}", "Factor:BRENT"))
    assert result.claims == [] and "+0.41" in result.removed_claims[0].reason


def test_a_number_from_a_failed_tool_call_does_not_count():
    """A call that ended in error supports nothing."""
    failed = ToolCall(tool="most_exposed", args={}, error="boom", result=[{"beta": 0.99}])
    result = verify(claim("Beta 0.99.", "estimate", f"Company:{PETROLIA}"), trace=[failed])
    assert result.claims == []


def test_evidence_must_exist_in_the_database():
    """An ISIN the graph does not know, an unknown label, or no evidence at all: removed."""
    unknown = claim("Beta 0.62.", "estimate", "Company:XX0000000000")
    label = claim("Beta 0.62.", "estimate", "User:42")
    bare = claim("Petrolia is an oil company.", "fact")
    result = verify(unknown, label, bare)
    assert result.claims == []
    assert [item.reason for item in result.removed_claims] == [
        "evidence not in the database: Company:XX0000000000",
        "evidence not in the database: User:42",
        "no evidence ref",
    ]
    assert result.answer == NOTHING_VERIFIED


def test_buy_or_sell_wording_is_removed():
    """Even with a verified number, advice never reaches the user."""
    result = verify(claim("Beta 0.62: you should buy Petrolia now.", "estimate", f"Company:{PETROLIA}"))
    assert result.claims == [] and "never advises" in result.removed_claims[0].reason


# --- truth levels ------------------------------------------------------------------------------------


def test_a_beta_stated_as_a_fact_is_lowered_to_an_estimate():
    """The number comes from a SENSITIVE_TO result (estimate), even though PRODUCES (fact) is also there."""
    result = verify(claim("Petrolia's beta to Brent is 0.62.", "fact", f"Company:{PETROLIA}", "Factor:BRENT"))
    assert result.claims[0].truth == "estimate"
    assert result.claims[0].reason == "stated as fact, its source is a estimate: lowered"


def test_a_declared_fact_without_numbers_stays_a_fact():
    """Petrolia produces Brent (through its industry): the strongest source naming both keys is a fact."""
    result = verify(claim("Petrolia produces Brent.", "fact", f"Company:{PETROLIA}", "Factor:BRENT"))
    assert result.claims[0].truth == "fact" and result.claims[0].reason is None


def test_a_news_claim_is_at_most_a_hypothesis():
    """Cuprum supplying Autoroute rests on Claim:c1: lowered from fact to hypothesis."""
    result = verify(claim("Cuprum supplies Autoroute.", "fact", "Claim:c1", f"Company:{CUPRUM}"))
    assert result.claims[0].truth == "hypothesis"


def test_a_model_output_is_a_prediction():
    """0.71 comes from PREDICTED_SENSITIVITY: an 'estimate' is lowered to 'prediction'."""
    result = verify(
        claim("The model gives Autoroute a 0.71 probability of EURUSD exposure.", "estimate",
              f"Company:{AUTOROUTE}", "Factor:EURUSD")
    )  # fmt: skip
    assert result.claims[0].truth == "prediction"


def test_a_result_without_truth_field_gets_the_level_its_fields_imply():
    """X-ray factor lines carry a weighted beta but no `truth`: still an estimate, never a fact."""
    xray = ToolCall(
        tool="portfolio_xray",
        args={"positions": [{"isin": PETROLIA, "amount_eur": 650}]},
        result={"total_eur": 650, "factors": [{"factor_code": "BRENT", "weighted_beta": 0.62, "covered_weight": 1}]},
    )
    result = verify(claim("The portfolio's weighted beta to Brent is 0.62.", "fact", "Factor:BRENT"), trace=[xray])
    assert result.claims[0].truth == "estimate"
    amount = verify(claim("The portfolio holds 650 EUR of Petrolia.", "fact", f"Company:{PETROLIA}"), trace=[xray])
    assert amount.claims[0].truth == "fact"  # 650 comes from the arguments and the total, which are facts


def test_a_shock_line_gives_its_truth_level_through_the_company():
    """The shock line names Petrolia, not Brent: the number and the company are enough to find it."""
    result = verify(claim("Petrolia gains 12.4 %.", "fact", f"Company:{PETROLIA}", "Factor:BRENT"))
    assert result.claims[0].truth == "estimate"


# --- causal wording ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Petrolia rises because Brent rises (beta 0.62).",
        "The 12.4 % gain is caused by the oil price.",
        "Petrolia monte à cause du Brent (bêta 0,62).",
        "La hausse de 12,4 % est due au pétrole.",
        "Petrolia gagne 12,4 % grâce au Brent.",
    ],
)
def test_causal_wording_on_an_estimate_is_removed(text):
    """A beta is co-movement: 'because' / 'caused by' / 'à cause de' never reach the user on an estimate."""
    result = verify(claim(text, "estimate", f"Company:{PETROLIA}", "Factor:BRENT"))
    assert result.claims == [] and "co-movement" in result.removed_claims[0].reason


def test_causal_wording_is_checked_after_lowering():
    """A beta claimed as a fact with 'because' is lowered to estimate first, then removed."""
    result = verify(claim("Petrolia rises because of Brent (beta 0.62).", "fact", f"Company:{PETROLIA}"))
    assert result.claims == [] and "estimate" in result.removed_claims[0].reason


# --- the answer as a whole ----------------------------------------------------------------------------


def test_prose_with_an_unverified_number_is_rebuilt_even_if_every_claim_is_fine():
    """The writer slipped 3.5 into the prose only: the prose becomes the verified claims."""
    good = claim("Petrolia's beta to Brent is 0.62.", "estimate", f"Company:{PETROLIA}", "Factor:BRENT")
    result = verify(good, text="Petrolia's beta is 0.62, so expect +3.5 % tomorrow.")
    assert result.claims == [good] and result.answer == good.text


def test_refusals_and_answers_without_claims_are_untouched():
    """Nothing to check: no database call."""
    client = KnownRefs()
    refusal = AgentAnswer(answer="I cannot advise.", refused=True, refusal_reason="advice")
    question = AgentAnswer(answer="Which Total do you mean: TotalEnergies SE or Total Gabon?")
    assert AnswerVerifier(client).verify(refusal) == refusal
    assert AnswerVerifier(client).verify(question) == question
    assert client.queries == []


def test_a_preview_is_used_when_the_full_result_is_missing():
    """Older traces only kept the preview: its JSON still supports the numbers."""
    call = ToolCall(tool="most_exposed", result_preview=json.dumps([{"company_isin": PETROLIA, "beta": 0.62}]))
    assert 0.62 in TraceFacts.from_answer(AgentAnswer(answer="", trace=[call])).numbers


def test_one_existence_query_per_label():
    """Three companies and a factor: two queries, not four."""
    client = KnownRefs()
    refs = {f"Company:{PETROLIA}", f"Company:{AEROLYS}", "Company:XX0000000000", "Factor:BRENT"}
    assert AnswerVerifier(client).existing_refs(refs) == refs - {"Company:XX0000000000"}
    assert len(client.queries) == 2


@pytest.mark.neo4j
def test_existing_refs_on_the_mini_graph(neo4j_client):
    """Real database: companies, factors, claims and articles that exist; one that does not."""
    refs = {
        f"Company:{PETROLIA}",
        "Factor:BRENT",
        "Claim:c1",
        "Article:https://example.org/news/cuprum-autoroute-contract",
        "Company:XX0000000000",
    }
    assert AnswerVerifier(neo4j_client).existing_refs(refs) == refs - {"Company:XX0000000000"}
