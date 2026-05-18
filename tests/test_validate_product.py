"""The validation gate must itself be trustworthy: on the committed
data every product invariant holds, and a report is written.
"""

import json

from src.features.guar_price import PROCESSED_DIR_DEFAULT
from src.validate_product import run_all


def test_all_product_invariants_hold_on_current_data():
    ok, results = run_all()
    failed = [c.name for c in results if not c.passed]
    assert ok, f"product validation failed: {failed}"
    assert len(results) >= 15  # a meaningful gate, not a token one


def test_report_artifact_written_and_consistent():
    ok, results = run_all()
    rpt = json.loads(
        (PROCESSED_DIR_DEFAULT / "product_validation_report.json").read_text()
    )
    assert rpt["passed"] == ok
    assert rpt["n_checks"] == len(results)
    assert rpt["n_failed"] == sum(1 for c in results if not c.passed)
    assert {"spine.coverage_72_months", "radar.top_pivot_not_us"} <= {
        c["name"] for c in rpt["checks"]
    }
