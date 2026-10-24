"""Train the sensitivity model and write PREDICTED_SENSITIVITY links (role C). Usage: python -m ml.train

Plan (C3): features from the graph (industry, declared PRODUCES/CONSUMES, FastRP embedding,
community, neighbours' betas) predict whether a stock's beta to a factor is significant, with a
temporal split (fit on the older window, evaluate on the recent one). Compared against a naive
baseline (industry majority). Metrics in docs/ml_report.md.
"""


def main() -> None:
    """Build the dataset, train, evaluate against the baseline, write predictions."""
    raise NotImplementedError("C3: ml.train")


if __name__ == "__main__":
    main()
