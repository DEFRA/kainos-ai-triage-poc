import unittest
from unittest.mock import Mock, patch

from backend.src.decision.implementations.s3_decision_agent import run_s3_decision


DECISION_MODULE = "backend.src.decision.implementations.s3_decision_agent"
RESULT = {"outcome": "no_objection", "confidence": "High"}


class S3DecisionAgentTests(unittest.TestCase):
    def setUp(self):
        self.s3 = Mock()
        self.s3.put_object.return_value = {"VersionId": "version-1"}

    def test_stores_decision_under_prefix_and_returns_storage(self):
        with patch(f"{DECISION_MODULE}.run_decision", return_value=dict(RESULT)) as decision:
            result = run_s3_decision(
                {"case_intake": {}},
                bucket="bucket",
                prefix="run-1/decision/",
                s3_client=self.s3,
            )

        decision.assert_called_once_with({"case_intake": {}})
        self.assertEqual(result["outcome"], "no_objection")
        self.assertEqual(result["storage"]["bucket"], "bucket")
        self.assertEqual(result["storage"]["key"], "run-1/decision/decision.json")
        self.s3.put_object.assert_called_once()

    def test_rejects_empty_bucket(self):
        with self.assertRaisesRegex(ValueError, "bucket"):
            run_s3_decision({}, bucket=" ", prefix="run-1/decision", s3_client=self.s3)

    def test_rejects_empty_prefix(self):
        with self.assertRaisesRegex(ValueError, "prefix"):
            run_s3_decision({}, bucket="bucket", prefix="/", s3_client=self.s3)

    def test_upload_failure_raises_runtime_error(self):
        self.s3.put_object.side_effect = RuntimeError("denied")
        with patch(f"{DECISION_MODULE}.run_decision", return_value=dict(RESULT)):
            with self.assertRaisesRegex(RuntimeError, "decision result"):
                run_s3_decision({}, bucket="bucket", prefix="run-1/decision", s3_client=self.s3)


if __name__ == "__main__":
    unittest.main()
