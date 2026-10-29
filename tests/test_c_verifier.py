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
    """Beta and r2 as the tool returned them (rounded): kept, no reason; the answer is the verified claim."""
    good = claim("Petrolia historically moved with Brent: beta 0.62, r2 48 %.", "estimate",
                 f"Company:{PETROLIA}", "Factor:BRENT")  # fmt: skip
    result = verify(good, text="Petrolia moved with Brent (beta 0.62).")
    assert result.claims == [good]
    assert result.removed_claims == []
    assert result.answer == good.text


@pytest.mark.parametrize(
    ("text", "isin"),
    [
        ("A +20 % Brent shock gives Petrolia +12.4 %.", PETROLIA),  # the shock size comes from the arguments
        ("AeroLys falls 8.2 % in that scenario.", AEROLYS),  # no sign written: the size is compared
        ("AeroLys: −0.41 beta to Brent.", AEROLYS),  # Unicode minus
        ("Petrolia beta 0,62.", PETROLIA),  # French decimal comma
        ("Two companies have an oil beta above r2 0.1.", PETROLIA),  # 2 is the list length; 0.1 an argument
    ],
)
def test_rounding_signs_and_formats_are_accepted(text, isin):
    """What a writer may legitimately do with a number the tool returned."""
    result = verify(claim(text, "estimate", f"Company:{isin}", "Factor:BRENT"))
    assert len(result.claims) == 1, result.removed_claims


def test_thousands_separators_are_read_both_ways():
    """'1,000 EUR' is a thousand (English), '0,62' a decimal (French): both match what the tool returned."""
    trade = ToolCall(tool="portfolio_xray", args={"positions": [{"isin": PETROLIA, "amount_eur": 1000}]},
                     result={"total_eur": 1000, "count": 2470})  # fmt: skip
    for text in ("Petrolia: a 1,000 EUR position.", "Petrolia: 1\u202f000 EUR.", "2,470 companies including Petrolia."):
        assert verify(claim(text, "fact", f"Company:{PETROLIA}"), trace=[trade]).claims, text
    assert not verify(claim("Petrolia: a 1,500 EUR position.", "fact", f"Company:{PETROLIA}"), trace=[trade]).claims


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


def test_a_number_of_another_company_does_not_support_the_claim():
    """0.62 is Petrolia's beta, not AeroLys's: citing AeroLys and Brent is not enough, the claim goes."""
    stolen = claim("AeroLys has a beta of 0.62 to Brent.", "estimate", f"Company:{AEROLYS}", "Factor:BRENT")
    result = verify(stolen, trace=[EXPOSED])
    assert result.claims == []
    assert "0.62" in result.removed_claims[0].reason
    same_row = claim("Petrolia has a beta of 0.62 to Brent.", "estimate", f"Company:{PETROLIA}", "Factor:BRENT")
    assert verify(same_row, trace=[EXPOSED]).claims == [same_row]


def test_a_shock_impact_belongs_to_its_line():
    """+12.4 % is Petrolia's line of the shock: it cannot be AeroLys's impact."""
    result = verify(claim("AeroLys gains 12.4 % if Brent rises 20 %.", "estimate", f"Company:{AEROLYS}",
                          "Factor:BRENT"), trace=[SHOCK])  # fmt: skip
    assert result.claims == [] and "12.4" in result.removed_claims[0].reason


def test_a_row_without_company_must_share_a_code_with_the_evidence():
    """A factor line (no ISIN) supports a claim on that factor, not one on another factor."""
    xray = ToolCall(
        tool="portfolio_xray",
        args={"positions": [{"isin": PETROLIA, "amount_eur": 650}]},
        result={"total_eur": 650, "factors": [{"factor_code": "BRENT", "weighted_beta": 0.62, "covered_weight": 1}]},
    )
    assert verify(claim("Weighted beta to Brent: 0.62.", "estimate", "Factor:BRENT"), trace=[xray]).claims
    assert not verify(claim("Weighted beta to EUR/USD: 0.62.", "estimate", "Factor:EURUSD"), trace=[xray]).claims


def test_a_claim_no_tool_result_is_about_is_removed():
    """No number to check, real refs, but nothing in the trace talks about AeroLys and EUR/USD: removed."""
    invented = claim("AeroLys is exposed to EUR/USD.", "fact", f"Company:{AEROLYS}", "Factor:EURUSD")
    result = verify(invented, trace=[EXPOSED, SHOCK])
    assert result.claims == [] and "no tool result is about" in result.removed_claims[0].reason
    assert verify(claim("AeroLys produces Brent.", "fact", f"Company:{AEROLYS}", "Factor:BRENT"), trace=[]).claims == []


def test_the_prose_is_always_the_verified_claims():
    """The prose gives Petrolia's 0.62 to AeroLys while the claim is right: the prose never reaches the user."""
    good = claim("Petrolia's beta to Brent is 0.62.", "estimate", f"Company:{PETROLIA}", "Factor:BRENT")
    result = verify(good, trace=[EXPOSED], text="AeroLys has a beta of 0.62 to Brent.")
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


@pytest.mark.parametrize(
    "text",
    [
        "Beta 0.62: you should buy Petrolia now.",
        "You should consider buying Petrolia (beta 0.62).",
        "It may be a good time to sell Petrolia (beta 0.62).",
        "Petrolia has a beta of 0.62. Sell before Brent falls.",
        "Vous devriez vendre Petrolia (bêta 0,62).",
        "Il serait intéressant de renforcer Petrolia (bêta 0,62).",
        "Allégez Petrolia (bêta 0,62).",
    ],
)
def test_buy_or_sell_wording_is_removed(text):
    """Even with a verified number, advice never reaches the user, in English or in French."""
    result = verify(claim(text, "estimate", f"Company:{PETROLIA}"))
    assert result.claims == [] and "never advises" in result.removed_claims[0].reason


@pytest.mark.parametrize(
    "text",
    ["Petrolia sells oil and has a beta of 0.62 to Brent.", "Airlines buy jet fuel: AeroLys beta −0.41 to Brent."],
)
def test_describing_what_a_company_buys_or_sells_is_not_advice(text):
    """'sells oil' describes a business, not an order to the user."""
    isin = AEROLYS if "AeroLys" in text else PETROLIA
    assert verify(claim(text, "estimate", f"Company:{isin}", "Factor:BRENT")).claims


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
        "Petrolia rose thanks to Brent (beta 0.62).",
        "Petrolia rose as a result of oil (beta 0.62).",
        "Petrolia monte en raison du Brent (bêta 0,62).",
        "Petrolia gagne 12,4 % suite à la hausse du Brent.",
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


def test_refusals_are_untouched():
    """Nothing to check: no database call."""
    client = KnownRefs()
    refusal = AgentAnswer(answer="I cannot advise.", refused=True, refusal_reason="advice")
    assert AnswerVerifier(client).verify(refusal) == refusal
    assert client.queries == []


def test_an_answer_without_claims_becomes_the_safe_answer():
    """The writer left the claims out but wrote numbers: nothing was checked, so none of it reaches the user."""
    unclaimed = AgentAnswer(answer="AeroLys has a beta of 0.62 and falls 30 %.", trace=[EXPOSED, SHOCK])
    result = AnswerVerifier(KnownRefs()).verify(unclaimed)
    assert result.answer == NOTHING_VERIFIED
    assert result.claims == [] and result.trace == unclaimed.trace


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
