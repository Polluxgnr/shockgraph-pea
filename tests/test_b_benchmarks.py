"""Fast tests for benchmark dataset and latency helpers."""

from scripts.bench_data import COMPANIES, CORRELATIONS, FUNDS, SENSITIVITIES, _batches
from scripts.benchmark import measure


def test_benchmark_contract_sizes_and_batches() -> None:
    """The generator meets minimum sizes and covers every row once."""
    assert COMPANIES >= 20_000
    assert CORRELATIONS >= 200_000
    assert SENSITIVITIES >= 100_000
    assert FUNDS == 50
    batches = list(_batches(2_501, 1_000))
    assert batches == [(0, 1_000), (1_000, 2_000), (2_000, 2_501)]


def test_measure_reports_ordered_real_samples() -> None:
    """Latency helper warms once and emits coherent p50/p95 values."""
    calls = []

    def probe() -> None:
        calls.append(1)

    result = measure(probe, 5)
    assert len(calls) == 6
    assert 0 <= result["min_ms"] <= result["p50_ms"] <= result["p95_ms"] <= result["max_ms"]
