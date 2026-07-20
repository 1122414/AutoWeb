from scripts.benchmark.reliability_lab import run_lab


def test_reliability_lab_fault_injections_are_all_closed():
    report = run_lab()

    assert report["total"] >= 6
    assert report["failed"] == 0
    assert report["passed"] == report["total"]
