import json
import unittest
from pathlib import Path

from html_report import generate_report
from tools.intent_classifier import classify_intent
from tools.mschema_builder import build_mschema
from tools.data_context import build_data_context, build_llm_data_context
from tools.evidence_planner import build_evidence_plan, execute_evidence_plan
from tools.query_builder import build_distribution_query, build_preview_query
from tools.report_planner import build_report_spec
from tools.report_quality import validate_report_spec
from tools.schema_interview import build_context_required_spec, detect_semantic_gaps
from tools.schema_linker import link_schema
from tools.semantic_inference import infer_column_roles
from tools.table_profiler import profile_from_rows


class GenericEngineTests(unittest.TestCase):
    def test_vietnamese_definition_intent(self):
        result = classify_intent("the nao la khach hang uy tin")
        self.assertEqual(result["intent"], "definition")
        self.assertTrue(result["requires_validation"])

    def test_semantic_roles_are_taxonomy_driven(self):
        roles = infer_column_roles({
            "name": "order_amount",
            "data_type": "double precision",
            "distinct_count": 20,
            "row_count": 100,
        })
        self.assertEqual(roles[0]["role"], "measure")

    def test_report_spec_warns_when_validation_is_missing(self):
        rows = [
            {"entity_id": 1, "segment": "A", "value": 10},
            {"entity_id": 2, "segment": "B", "value": 20},
            {"entity_id": 3, "segment": "A", "value": 15},
        ]
        profile = profile_from_rows(rows, ["entity_id", "segment", "value"])
        intent = classify_intent("define reliable entities")
        spec = build_report_spec("define reliable entities", intent, profile, len(rows))

        self.assertEqual(spec["evidence_level"], "proxy_based")
        self.assertTrue(any("outcome" in warning.lower() for warning in spec["warnings"]))
        self.assertTrue(spec["summary_cards"])
        self.assertLessEqual(len(spec["slicers"]), 2)
        self.assertLessEqual(len(spec["charts"]), 2)
        self.assertEqual(len(spec.get("executive_points", [])), 5)
        self.assertLessEqual(len(spec.get("candidate_signals", [])), 5)
        self.assertTrue(spec.get("evidence_plan"))
        self.assertTrue(spec.get("evidence_results"))
        self.assertFalse(any(issue["severity"] == "blocker" for issue in validate_report_spec(spec)))

    def test_data_context_is_single_table_but_list_based(self):
        rows = [{"customer_id": 1, "segment": "A", "amount": 10}]
        profile = profile_from_rows(rows, ["customer_id", "segment", "amount"])
        context = build_data_context("uploaded_table", profile, rows, ["customer_id", "segment", "amount"])
        llm_context = build_llm_data_context(context, focus_columns=["segment", "amount"])

        self.assertEqual(context["mode"], "single_table")
        self.assertEqual(len(context["tables"]), 1)
        self.assertTrue(context["constraints"]["single_table_only"])
        self.assertLess(len(json.dumps(llm_context, ensure_ascii=False)), 8000)

    def test_schema_linking_and_evidence_plan_are_generic(self):
        rows = [
            {"customer_id": 1, "segment": "A", "amount": 10},
            {"customer_id": 2, "segment": "B", "amount": 20},
            {"customer_id": 3, "segment": "A", "amount": 15},
        ]
        profile = profile_from_rows(rows, ["customer_id", "segment", "amount"])
        profile["table_name"] = "sample_table"
        linked = link_schema("compare amount by segment", profile, {}, limit=5)
        plan = build_evidence_plan("compare amount by segment", classify_intent("compare amount by segment"), profile, {}, linked)

        self.assertTrue(linked)
        self.assertTrue(any(item.get("sql") for item in plan["items"]))
        self.assertIn('"sample_table"', build_preview_query("sample_table"))
        self.assertIn('GROUP BY "segment"', build_distribution_query("sample_table", "segment"))

    def test_html_report_smoke(self):
        rows = [
            {"entity_id": 1, "segment": "A", "value": 10},
            {"entity_id": 2, "segment": "B", "value": 20},
        ]
        profile = profile_from_rows(rows, ["entity_id", "segment", "value"])
        intent = classify_intent("segment entities")
        spec = build_report_spec("segment entities", intent, profile, len(rows))
        path = generate_report(
            question="segment entities",
            sql_query='SELECT * FROM "sample"',
            raw_data=rows,
            columns=["entity_id", "segment", "value"],
            analytics={"summary": "ok"},
            report_spec=spec,
            table_profile=profile,
        )

        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("Maxxem Data Analysis Report", html)
        self.assertIn("id=\"chart-grid\"", html)
        self.assertIn("SQL Evidence", html)
        self.assertNotIn("id=\"sample-table\"", html)
        self.assertNotIn("<table", html.lower())
        self.assertIn("report-spec-json", html)
        json.loads(html.split('id="report-spec-json">', 1)[1].split("</script>", 1)[0])

    def test_context_required_report_has_no_dashboard_noise(self):
        rows = [
            {"entity_id": 1, "segment": "A", "score": 0.8},
            {"entity_id": 2, "segment": "B", "score": 0.4},
        ]
        profile = profile_from_rows(rows, ["entity_id", "segment", "score"])
        intent = classify_intent("which columns determine good customers")
        context = {
            "table_purpose": "",
            "row_grain": "",
            "primary_metric": "",
            "outcome_column": "",
            "positive_outcome_value": "",
            "column_descriptions": {},
        }
        mschema = build_mschema(profile, context)
        gap_result = detect_semantic_gaps("which columns determine good customers", intent, profile, context)
        self.assertTrue(gap_result["clarification_required"])

        spec = build_context_required_spec(
            "which columns determine good customers",
            intent,
            profile,
            context,
            mschema,
            gap_result["gaps"],
        )
        path = generate_report(
            question="which columns determine good customers",
            sql_query="",
            raw_data=[],
            columns=[],
            analytics={"summary": ""},
            report_spec=spec,
            table_profile=profile,
        )
        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("Context Required", html)
        self.assertIn("Required Questions", html)
        self.assertNotIn("id=\"slicers\"", html)
        self.assertNotIn("id=\"chart-grid\"", html)
        self.assertNotIn("<table", html.lower())
        self.assertIn("report-spec-json", html)


if __name__ == "__main__":
    unittest.main()
