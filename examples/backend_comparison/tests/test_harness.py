import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness as h


def synthetic_result(fixture):
    """Valid protocol data, independent of the real model's numerical behavior."""
    result = {"schema": 1, "codec": [], "gates": [0, 0, 0, 0, 0, 0, 0, 1, 2, 0, 0, 0], "rows": []}
    for raw in fixture["raw"]:
        result["codec"].append({"bytes": None if raw is None else list(h.struct.pack("<i", raw)),
                                "token": raw//4000 if raw is not None and 0 <= raw < 128000 else -1})
    for c in fixture["cases"]:
        params = sum((c["net"][key] for key in ("w1", "b1", "w2", "b2")), [])
        for mode in h.MODES:
            result["rows"].append({"id": c["id"], "mode": mode, "code": 0 if c["discrete"] else -1,
                                   "argmax": 0, "surprise": 5.0, "probabilities": [1/32]*32,
                                   "parameters": params.copy()})
    return result


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = h.read_json(h.ROOT / "fixture.json")
        self.good = synthetic_result(self.fixture)

    def test_accepts_small_numeric_difference(self):
        other = copy.deepcopy(self.good)
        other["rows"][2]["parameters"][-1] += 1e-7
        self.assertTrue(h.compare_results(self.good, other, self.fixture)["pass"])

    def test_rejects_outside_tolerance(self):
        other = copy.deepcopy(self.good)
        other["rows"][2]["parameters"][-1] += 0.01
        with self.assertRaisesRegex(ValueError, "differs"):
            h.compare_results(self.good, other, self.fixture)

    def test_rejects_any_discrete_code_change(self):
        other = copy.deepcopy(self.good)
        other["rows"][12]["code"] = 1
        with self.assertRaisesRegex(ValueError, "discrete code"):
            h.compare_results(self.good, other, self.fixture)

    def test_rejects_encoder_change_below_numeric_tolerance(self):
        other = copy.deepcopy(self.good)
        other["rows"][1]["parameters"][0] += 1e-7
        with self.assertRaisesRegex(ValueError, "encoder"):
            h.compare_results(self.good, other, self.fixture)

    def test_rejects_nonfinite_values(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            other = copy.deepcopy(self.good)
            other["rows"][2]["parameters"][0] = bad
            with self.assertRaisesRegex(ValueError, "finite"):
                h.compare_results(self.good, other, self.fixture)

    def test_rejects_step_larger_than_clipped_bound(self):
        other = copy.deepcopy(self.good)
        other["rows"][2]["parameters"][-1] += 0.04
        with self.assertRaisesRegex(ValueError, "step bound"):
            h.compare_results(self.good, other, self.fixture)

    def test_rejects_missing_rows_and_bad_distributions(self):
        other = copy.deepcopy(self.good)
        other["rows"].pop()
        with self.assertRaisesRegex(ValueError, "row count"):
            h.compare_results(self.good, other, self.fixture)
        other = copy.deepcopy(self.good)
        other["rows"][0]["probabilities"][0] = 0.5
        with self.assertRaisesRegex(ValueError, "distribution"):
            h.compare_results(self.good, other, self.fixture)

    def test_rejects_wrong_target_surprise(self):
        other = copy.deepcopy(self.good)
        other["rows"][0]["surprise"] = 4.0
        with self.assertRaisesRegex(ValueError, "surprise"):
            h.compare_results(self.good, other, self.fixture)

    def test_rejects_wrong_codec_and_gate(self):
        for key in ("codec", "gates"):
            other = copy.deepcopy(self.good)
            if key == "codec":
                other[key][0]["token"] = 0
            else:
                other[key][0] = 2
            with self.assertRaisesRegex(ValueError, "mismatch"):
                h.compare_results(self.good, other, self.fixture)

    def test_rejects_mismatched_reports(self):
        report = {"report_schema": 1, "fixture_sha256": h.sha(h.ROOT / "fixture.json"),
                  "source_sha256": h.source_hash(), "bend_commit": h.PIN["bend_commit"],
                  "reference": self.good, "result": self.good}
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/"a.json", Path(directory)/"b.json"
            a.write_text(json.dumps(report))
            b.write_text(json.dumps(report))
            self.assertTrue(h.compare_reports([a, b])[0]["pass"])
            for field in ("fixture_sha256", "source_sha256", "bend_commit"):
                changed = {**report, field: "different"}
                b.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, "different"):
                    h.compare_reports([a, b])

    def test_literals_roundtrip_f32(self):
        for x in (0.0, -0.0, 0.03, -1e-9, 1e-20, 1.4e-45, 0.123456789, -8.25):
            text = h.literal(x)
            value = -float(text[8:-1]) if text.startswith("F32.neg(") else float(text)
            self.assertEqual(h.struct.pack("<f", x), h.struct.pack("<f", value))


if __name__ == "__main__":
    unittest.main()
