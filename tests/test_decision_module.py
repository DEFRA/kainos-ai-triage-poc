import copy
import unittest

from backend.src.decision.implementations.decision_module import run_decision


class DecisionModuleTests(unittest.TestCase):
    def setUp(self):
        signal = {
            "classification": "no_signal",
            "confidence": "high",
            "reason": "No positive signal identified.",
            "evidence": [{"source": "consultation email", "quote": "Proposal description"}],
        }
        self.inputs = {
            "case_intake": {
                field: copy.deepcopy(signal) for field in (
                    "eia_screening", "eia_scoping", "amendment", "conditions",
                    "pre_application", "prior_approval", "minerals_waste_existing",
                    "minerals_waste_new",
                )
            },
            "document_screening": {
                field: copy.deepcopy(signal)
                for field in ("minerals_waste_existing", "minerals_waste_new")
            },
            "spatial_screening": {
                "best_and_most_versatile_land": {
                    "intersects": False, "overlap_area_hectares": 0,
                },
                "answers": [
                    {
                        "category": category, "value": 0, "text": "Not applicable",
                        "confidence": "High", "reasoning": "Development is not applicable.",
                        "supporting_evidence": [{"source": "planning statement", "evidence": "Housing"}],
                    }
                    for category in ("All Consultations", "Residential")
                ],
            },
        }

    def test_all_no_returns_unnecessary_without_reasoning_or_mutation(self):
        original = copy.deepcopy(self.inputs)
        result = run_decision(self.inputs)
        self.assertEqual(set(result), {"classification", "confidence", "checklist"})
        self.assertEqual(result["classification"], "unnecessary")
        self.assertIsNone(result["confidence"])
        self.assertEqual([item["guidance_id"] for item in result["checklist"]], [
            "1b", "1c", "1d", "1e", "1f", "1g", "1h", "1i", "1j", "1k",
        ])
        self.assertTrue(all(item["answer"] is False for item in result["checklist"]))
        self.assertEqual(self.inputs, original)

    def test_each_signal_can_independently_trigger_its_rule(self):
        for stage in ("case_intake", "document_screening"):
            for field in self.inputs[stage]:
                with self.subTest(stage=stage, field=field):
                    inputs = copy.deepcopy(self.inputs)
                    inputs[stage][field]["classification"] = "yes"
                    result = run_decision(inputs)
                    self.assertEqual(result["classification"], "necessary")
                    self.assertEqual(sum(item["answer"] for item in result["checklist"]), 1)

    def test_land_threshold_and_intersection(self):
        for intersects, area, expected in (
            (True, 19.9, False), (True, 20, False), (True, 20.1, True), (False, 23.4, False),
        ):
            with self.subTest(intersects=intersects, area=area):
                self.inputs["spatial_screening"]["best_and_most_versatile_land"].update(
                    intersects=intersects, overlap_area_hectares=area
                )
                result = run_decision(self.inputs)
                self.assertEqual(result["checklist"][7]["answer"], expected)
                self.assertIsNone(result["checklist"][7]["confidence"])

    def test_irz_values_control_answers_not_text(self):
        for category_index, value, expected_designation in ((0, 1, True), (0, 2, False), (1, 6, False)):
            with self.subTest(category_index=category_index, value=value):
                inputs = copy.deepcopy(self.inputs)
                inputs["spatial_screening"]["answers"][category_index]["value"] = value
                result = run_decision(inputs)
                self.assertEqual(result["checklist"][8]["answer"], expected_designation)
                self.assertTrue(result["checklist"][9]["answer"])

    def test_metadata_is_preserved_and_irz_metadata_is_normalized(self):
        result = run_decision(self.inputs)
        signal_item = result["checklist"][0]
        self.assertIn("No positive signal identified.", signal_item["reason"])
        self.assertEqual(signal_item["confidence"], "high")
        self.assertEqual(signal_item["evidence"][0], {
            "stage": "case_intake", "source": "consultation email", "quote": "Proposal description",
        })
        irz_item = result["checklist"][-1]
        self.assertEqual(irz_item["confidence"], "high")
        self.assertEqual(irz_item["evidence"][0], {
            "stage": "spatial_screening", "source": "planning statement", "quote": "Housing",
        })

    def test_item_confidence_does_not_change_classification(self):
        self.inputs["case_intake"]["eia_screening"].update(classification="yes", confidence="low")
        result = run_decision(self.inputs)
        self.assertEqual(result["classification"], "necessary")
        self.assertEqual(result["checklist"][0]["confidence"], "low")

    def test_multi_signal_confidence_follows_or_logic(self):
        rules = (
            ("1b", ("case_intake", "eia_screening"), ("case_intake", "eia_scoping")),
            ("1g", ("case_intake", "minerals_waste_existing"), ("document_screening", "minerals_waste_existing")),
            ("1h", ("case_intake", "minerals_waste_new"), ("document_screening", "minerals_waste_new")),
        )
        cases = (
            (True, "high", False, "low", "high"),
            (False, "low", True, "high", "high"),
            (True, "low", False, "high", "low"),
            (True, "medium", True, "high", "high"),
            (False, "high", False, "low", "low"),
            (False, "high", False, "medium", "medium"),
            (True, "medium", False, None, "medium"),
            (True, None, True, "medium", "medium"),
            (True, None, False, "high", None),
            (False, None, False, "high", None),
        )
        for guidance_id, first, second in rules:
            for first_yes, first_confidence, second_yes, second_confidence, expected in cases:
                with self.subTest(guidance_id=guidance_id, case=(first_yes, first_confidence, second_yes, second_confidence)):
                    inputs = copy.deepcopy(self.inputs)
                    for (stage, field), positive, confidence in (
                        (first, first_yes, first_confidence),
                        (second, second_yes, second_confidence),
                    ):
                        inputs[stage][field].update(
                            classification="yes" if positive else "no_signal", confidence=confidence
                        )
                    result = run_decision(inputs)
                    item = next(item for item in result["checklist"] if item["guidance_id"] == guidance_id)
                    self.assertEqual(item["answer"], first_yes or second_yes)
                    self.assertEqual(item["confidence"], expected)
                    self.assertEqual(set(item), {"guidance_id", "answer", "confidence", "reason", "evidence"})

    def test_irz_confidence_follows_or_logic(self):
        cases = (
            (0, "High", 1, "Low", "low"),
            (1, "High", 0, "Low", "high"),
            (1, "Low", 2, "Medium", "medium"),
            (0, "High", 0, "Low", "low"),
            (0, "High", 0, None, None),
            (1, "High", 0, None, "high"),
            (1, None, 2, "Medium", "medium"),
            (0, "High", 2, None, None),
        )
        for first_value, first_confidence, second_value, second_confidence, expected in cases:
            with self.subTest(case=(first_value, first_confidence, second_value, second_confidence)):
                inputs = copy.deepcopy(self.inputs)
                answers = inputs["spatial_screening"]["answers"]
                answers[0].update(value=first_value, confidence=first_confidence)
                answers[1].update(value=second_value, confidence=second_confidence)
                result = run_decision(inputs)
                self.assertEqual(result["checklist"][-1]["confidence"], expected)
                self.assertEqual(result["checklist"][-2]["confidence"], first_confidence.lower() if first_confidence else None)

    def test_necessary_confidence_uses_strongest_positive_item(self):
        self.inputs["case_intake"]["amendment"].update(classification="yes", confidence="low")
        self.inputs["case_intake"]["conditions"].update(classification="yes", confidence="medium")
        self.inputs["spatial_screening"]["best_and_most_versatile_land"].update(
            intersects=True, overlap_area_hectares=23.4
        )
        result = run_decision(self.inputs)
        self.assertEqual(result["classification"], "necessary")
        self.assertEqual(result["confidence"], "medium")
        self.inputs["case_intake"]["conditions"]["confidence"] = "high"
        self.assertEqual(run_decision(self.inputs)["confidence"], "high")

    def test_unnecessary_confidence_uses_weakest_negative_item(self):
        self.inputs["spatial_screening"]["best_and_most_versatile_land"]["confidence"] = "high"
        for confidence in ("high", "medium", "low", None):
            with self.subTest(confidence=confidence):
                self.inputs["case_intake"]["conditions"]["confidence"] = confidence
                result = run_decision(self.inputs)
                self.assertEqual(result["classification"], "unnecessary")
                self.assertEqual(result["confidence"], confidence)

    def test_land_confidence_is_not_invented_for_positive_result(self):
        land = self.inputs["spatial_screening"]["best_and_most_versatile_land"]
        land.update(intersects=True, overlap_area_hectares=23.4)
        result = run_decision(self.inputs)
        self.assertEqual(result["classification"], "necessary")
        self.assertIsNone(result["confidence"])
        land["confidence"] = "medium"
        result = run_decision(self.inputs)
        self.assertEqual(result["checklist"][7]["confidence"], "medium")
        self.assertEqual(result["confidence"], "medium")

    def test_invalid_or_missing_signal_is_an_error(self):
        for invalid in (None, {}, {"classification": "unknown"}, {"classification": True}):
            with self.subTest(invalid=invalid):
                self.inputs["case_intake"]["amendment"] = invalid
                with self.assertRaises(ValueError):
                    run_decision(self.inputs)
        del self.inputs["case_intake"]["amendment"]
        with self.assertRaises(ValueError):
            run_decision(self.inputs)

    def test_invalid_area_is_an_error(self):
        for area in (None, "23", True, -1, float("nan"), float("inf")):
            with self.subTest(area=area):
                self.inputs["spatial_screening"]["best_and_most_versatile_land"]["overlap_area_hectares"] = area
                with self.assertRaises(ValueError):
                    run_decision(self.inputs)

    def test_invalid_irz_value_is_an_error(self):
        for value in (None, "unknown", "0", True, -1, 1.5):
            with self.subTest(value=value):
                self.inputs["spatial_screening"]["answers"][0]["value"] = value
                with self.assertRaises(ValueError):
                    run_decision(self.inputs)

    def test_empty_duplicate_or_missing_all_consultations_is_an_error(self):
        answers = self.inputs["spatial_screening"]["answers"]
        for invalid in (None, [], [answers[1]], [answers[0], answers[0]]):
            with self.subTest(invalid=invalid):
                self.inputs["spatial_screening"]["answers"] = invalid
                with self.assertRaises(ValueError):
                    run_decision(self.inputs)

    def test_invalid_metadata_is_an_error(self):
        for field, value in (("confidence", "certain"), ("reason", None), ("evidence", [{}])):
            with self.subTest(field=field):
                inputs = copy.deepcopy(self.inputs)
                inputs["case_intake"]["amendment"][field] = value
                with self.assertRaises(ValueError):
                    run_decision(inputs)


if __name__ == "__main__":
    unittest.main()