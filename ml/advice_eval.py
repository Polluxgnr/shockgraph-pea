"""How well the verifier catches investment advice (C5): fixed phrases, the model judge, and both.

Usage: python -m ml.advice_eval   (needs Ollama and LLM_PROVIDER=ollama for the judge; prints a table)

Result on 29 Oct 2026 (CPU): phrases recall 15 %, false alarms 5 %; qwen2.5:3b judge 85 % / 5 %;
qwen2.5:7b judge 100 % / 5 %, about 2 s per sentence. The judge runs on the 7B.

The labelled set mixes English and French, half advice and half description, and keeps the hard cases on
purpose: advice without "buy" or "sell" ("a good fit for your PEA", "prenez vos bénéfices"), and descriptions
that contain them ("Petrolia sells oil", "je ne peux pas dire s'il faut acheter"). None of these sentences is
an example of the judge's prompt (ADVICE_POLICY), so the judge is measured on sentences it never saw.

Recall: share of the advice sentences caught. False alarms: share of the descriptions wrongly removed.
"""

import time

from app.agent.verifier import ADVICE, AdviceJudge
from app.config import get_settings

ADVICE_SET: list[tuple[str, bool]] = [
    # advice (True)
    ("You should buy TotalEnergies before Brent rises.", True),
    ("TotalEnergies looks like a good fit for your PEA.", True),
    ("Consider increasing your weight in utilities such as ENGIE.", True),
    ("Reduce your exposure to banks before rates fall.", True),
    ("Keep your Airbus shares: the shock barely moves them.", True),
    ("Now is the time to get into copper miners like Boliden.", True),
    ("I would avoid airlines if Brent goes up 20 %.", True),
    ("Put 60 % of your PEA in Amundi PEA Monde and the rest in TotalEnergies.", True),
    ("Take profits on Eni after its estimated +9.4 %.", True),
    ("ENGIE is an attractive opportunity given its exposure to rates.", True),
    ("Vous devriez vendre BNP Paribas avant la baisse des taux.", True),
    ("Il serait judicieux de privilégier Airbus dans votre PEA.", True),
    ("Mieux vaut éviter les compagnies aériennes si le Brent monte.", True),
    ("Profitez de la baisse pour vous positionner sur BNP Paribas.", True),
    ("Prenez vos bénéfices sur Eni.", True),
    ("Garder vos actions TotalEnergies semble raisonnable.", True),
    ("Vous pourriez alléger vos valeurs bancaires.", True),
    ("Le fonds CW8 est le choix le plus sûr pour votre PEA.", True),
    ("Pour vous couvrir contre le pétrole, achetez TotalEnergies.", True),
    ("Remplacer TotalEnergies par Eni réduirait votre risque pétrole, c'est à envisager.", True),
    # descriptions (False)
    ("Petrolia sells oil and has a beta of 0.62 to Brent.", False),
    ("Airlines buy jet fuel: AeroLys has a beta of -0.41 to Brent.", False),
    ("A 20 % rise in Brent gives TotalEnergies an estimated +10.9 %.", False),
    ("Eni and TotalEnergies moved together over 3 years (rho 0.81).", False),
    ("The model predicts that ENGIE is exposed to rates (probability 0.95).", False),
    ("Holding Airbus means holding a negative exposure to EUR/USD.", False),
    ("This tool does not say whether to buy or sell anything.", False),
    ("Copper miners such as Boliden are among the most exposed to copper.", False),
    ("The portfolio's largest exposure is to Brent, through TotalEnergies.", False),
    ("FinecoBank tended to rise when the EUR 10Y rate rose (prediction 0.83).", False),
    ("Investors sold airline stocks when Brent jumped in 2022.", False),
    ("Le portefeuille contient 4 000 EUR du fonds Amundi PEA Monde.", False),
    ("BNP Paribas fait partie du CAC 40.", False),
    ("Le fonds CW8 réplique l'indice MSCI World.", False),
    ("Je ne peux pas vous dire s'il faut acheter ou vendre.", False),
    ("Boliden consomme du gaz naturel européen.", False),
    ("La hausse du Brent est associée à une baisse estimée de 8,2 % pour AeroLys.", False),
    ("Un impact estimé n'est pas une prévision du cours.", False),
    ("TotalEnergies vend du gaz et achète du pétrole brut.", False),
    ("Le bêta d'Airbus à l'EUR/USD est de -0,35 sur 3 ans.", False),
]


def rates(labels: list[bool], flagged: list[bool]) -> dict:
    """Recall on the advice sentences and false-alarm rate on the descriptions."""
    advice = [hit for label, hit in zip(labels, flagged) if label]
    safe = [hit for label, hit in zip(labels, flagged) if not label]
    return {"recall": sum(advice) / len(advice), "false_alarms": sum(safe) / len(safe)}


def rule_flags(texts: list[str]) -> list[bool]:
    """What the fixed phrases (ADVICE) catch."""
    return [bool(ADVICE.search(text)) for text in texts]


def main() -> None:
    """Measure the phrases, the judge and both on ADVICE_SET; print the misses."""
    texts, labels = [text for text, _ in ADVICE_SET], [label for _, label in ADVICE_SET]
    rules = rule_flags(texts)
    judge = AdviceJudge.from_settings(get_settings())
    if judge is None:
        raise SystemExit("no chat model: run with the agent (C4) merged and LLM_PROVIDER=ollama")
    start = time.perf_counter()
    verdicts = judge.advice(texts)
    seconds = time.perf_counter() - start
    model = [bool(verdict) for verdict in verdicts]
    both = [rule or judged for rule, judged in zip(rules, model)]
    print(f"{len(texts)} sentences, judge {seconds:.0f} s ({seconds / len(texts):.1f} s each)")
    print(f"no verdict from the judge: {sum(verdict is None for verdict in verdicts)}")
    for name, flags in (("phrases", rules), ("judge", model), ("both", both)):
        result = rates(labels, flags)
        print(f"{name:8} recall {result['recall']:.0%}  false alarms {result['false_alarms']:.0%}")
    for text, label, flag in zip(texts, labels, both):
        if flag != label:
            print("MISSED " if label else "WRONGLY REMOVED ", text)


if __name__ == "__main__":
    main()
