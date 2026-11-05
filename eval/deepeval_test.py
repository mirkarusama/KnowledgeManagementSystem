"""DeepEval CI gate (optional): `deepeval test run eval/deepeval_test.py` after `python eval/run_eval.py`.
Uses the self-hosted judge through LiteLLM; set DEEPEVAL to use a custom model as documented by DeepEval."""
import json
from pathlib import Path

import pytest

try:
    from deepeval import assert_test
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase
except ImportError:  # optional dependency
    pytest.skip("deepeval not installed (pip install .[eval])", allow_module_level=True)

REPORT = Path(__file__).with_name("report.json")
CASES = [r for r in json.loads(REPORT.read_text())["results"] if not r["fallback"]] if REPORT.exists() else []


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_faithfulness(case):
    tc = LLMTestCase(input=case["question"], actual_output=case["answer"], retrieval_context=case["contexts"])
    assert_test(tc, [FaithfulnessMetric(threshold=0.8)])
