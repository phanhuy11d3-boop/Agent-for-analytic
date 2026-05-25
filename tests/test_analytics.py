import json
import random
import string
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from html_report import generate_report
from tools import context_store
from tools.intent_classifier import classify_intent
from tools.mschema_builder import build_mschema
from tools.data_context import build_data_context, build_llm_data_context
from tools.evidence_planner import build_evidence_plan, execute_evidence_plan, _rebuild_simplified_sql
from tools.query_builder import build_distribution_query, build_preview_query
from tools.report_planner import build_report_spec
from tools.report_quality import validate_report_spec
from tools.answerability import evaluate_answerability
from tools.schema_interview import build_context_required_spec, detect_semantic_gaps
from tools.schema_linker import link_schema
from tools.semantic_proposer import propose_semantic_context
from tools.semantic_inference import infer_column_roles
from tools.table_profiler import profile_from_rows
from tools.metric_dimension_planner import build_explicit_aggregate_plan
from tools.observation_engine import build_observations


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
        self.assertIn("Maxxem Data Analysis", html)
        self.assertIn("id=\"chart-grid\"", html)
        self.assertIn("SQL Evidence", html)
        self.assertNotIn("id=\"sample-table\"", html)
        self.assertNotIn("<table", html.lower())
        self.assertIn("report-spec-json", html)
        json.loads(html.split('id="report-spec-json">', 1)[1].split("</script>", 1)[0])

    def test_semantic_proposal_detects_easy_outcome_dataset(self):
        rows = [
            {"customer_id": 1, "signup_channel": "Ads", "plan_tier": "Pro", "monthly_revenue": 99, "churned": 0},
            {"customer_id": 2, "signup_channel": "Referral", "plan_tier": "Basic", "monthly_revenue": 29, "churned": 1},
            {"customer_id": 3, "signup_channel": "Ads", "plan_tier": "Pro", "monthly_revenue": 89, "churned": 0},
            {"customer_id": 4, "signup_channel": "Organic", "plan_tier": "Basic", "monthly_revenue": 19, "churned": 1},
        ]
        profile = profile_from_rows(rows, ["customer_id", "signup_channel", "plan_tier", "monthly_revenue", "churned"])
        profile["table_name"] = "ds_customer_subscriptions"

        proposal = propose_semantic_context(profile, "which channels have highest churn", use_llm=False)

        self.assertEqual(proposal["context"]["outcome_column"], "churned")
        self.assertEqual(str(proposal["context"]["positive_outcome_value"]), "1")
        self.assertEqual(proposal["context"]["primary_metric"], "monthly_revenue")

    def test_answerability_blocks_outcome_question_without_outcome_but_allows_proxy(self):
        rows = [
            {"applicant_id": 1, "income": 1000, "external_score": 0.8, "segment": "A"},
            {"applicant_id": 2, "income": 500, "external_score": 0.2, "segment": "B"},
        ]
        profile = profile_from_rows(rows, ["applicant_id", "income", "external_score", "segment"])
        intent = classify_intent("define good credit risk customers")
        context = {"table_purpose": "", "row_grain": "", "outcome_column": "", "positive_outcome_value": ""}
        proposal = propose_semantic_context(profile, "define good credit risk customers", context, use_llm=False)

        blocked = evaluate_answerability("define good credit risk customers", intent, profile, context, proposal)
        proxy = evaluate_answerability("which groups look better based on available proxy fields", intent, profile, context, proposal)

        self.assertEqual(blocked["output_type"], "cannot_answer")
        self.assertTrue(blocked["should_stop"])
        self.assertEqual(proxy["output_type"], "exploratory_profile")
        self.assertFalse(proxy["should_stop"])

    def test_evaluative_segmentation_requires_outcome_unless_proxy_is_explicit(self):
        rows = [
            {"applicant_id": 1, "income": 1000, "external_score": 0.8, "segment": "A"},
            {"applicant_id": 2, "income": 500, "external_score": 0.2, "segment": "B"},
        ]
        profile = profile_from_rows(rows, ["applicant_id", "income", "external_score", "segment"])
        context = {"table_purpose": "", "row_grain": "", "outcome_column": "", "positive_outcome_value": ""}
        question = "Nhung nhom khach hang nao co ho so tin dung tot hon?"
        intent = classify_intent(question)
        proposal = propose_semantic_context(profile, question, context, use_llm=False)

        blocked = evaluate_answerability(question, intent, profile, context, proposal)
        proxy = evaluate_answerability(question + " dua tren cac proxy hien co", intent, profile, context, proposal)

        self.assertEqual(blocked["output_type"], "cannot_answer")
        self.assertTrue(blocked["should_stop"])
        self.assertEqual(proxy["output_type"], "exploratory_profile")
        self.assertFalse(proxy["should_stop"])

    def test_unconfirmed_saved_context_is_not_treated_as_truth(self):
        rows = [
            {"entity_id": 1, "segment": "A", "target": 1},
            {"entity_id": 2, "segment": "B", "target": 0},
        ]
        profile = profile_from_rows(rows, ["entity_id", "segment", "target"])
        profile["table_name"] = "sample_target_table"
        question = "define good entities"
        intent = classify_intent(question)
        previous_store = context_store.STORE_PATH
        with tempfile.TemporaryDirectory() as tmp:
            context_store.STORE_PATH = Path(tmp) / "semantic_contexts.json"
            try:
                draft = context_store.save_context("sample_target_table", profile, {
                    "table_purpose": "Entity outcomes",
                    "row_grain": "One row per entity",
                    "outcome_column": "target",
                    "positive_outcome_value": "1",
                })
                self.assertFalse(draft["confirmed"])
                blocked = evaluate_answerability(question, intent, profile, draft, {})
                self.assertTrue(blocked["should_stop"])

                confirmed = context_store.save_context("sample_target_table", profile, {
                    **draft,
                    "confirmed": True,
                })
                self.assertTrue(confirmed["confirmed"])
                allowed = evaluate_answerability(question, intent, profile, confirmed, {})
                self.assertFalse(allowed["should_stop"])
            finally:
                context_store.STORE_PATH = previous_store

    def test_direct_answer_output_suppresses_dashboard_noise(self):
        rows = [
            {"entity_id": 1, "segment": "A", "amount": 10},
            {"entity_id": 2, "segment": "B", "amount": 20},
            {"entity_id": 3, "segment": "A", "amount": 15},
        ]
        profile = profile_from_rows(rows, ["entity_id", "segment", "amount"])
        intent = classify_intent("compare amount by segment")
        spec = build_report_spec(
            "compare amount by segment",
            intent,
            profile,
            len(rows),
            answerability={
                "output_type": "direct_answer",
                "status": "answerable_with_warning",
                "reason": "Primary metric is inferred, not confirmed.",
                "warnings": ["No confirmed primary metric is set."],
            },
        )

        self.assertEqual(spec["charts"], [])
        self.assertEqual(spec["slicers"], [])
        self.assertEqual(spec["candidate_signals"], [])
        self.assertTrue(spec["claim_contract"])

    def test_metric_dimension_question_generates_answerable_report(self):
        rows = [
            {"region": "Central", "category": "Furniture", "sales": 100, "profit": -5},
            {"region": "Central", "category": "Technology", "sales": 220, "profit": 40},
            {"region": "East", "category": "Furniture", "sales": 120, "profit": 10},
            {"region": "East", "category": "Technology", "sales": 300, "profit": 70},
            {"region": "West", "category": "Office Supplies", "sales": 260, "profit": 85},
            {"region": "West", "category": "Technology", "sales": 250, "profit": 60},
        ]
        question = "Danh muc san pham nao co doanh thu cao nhat? Danh muc nao co loi nhuan cao nhat? chia theo vi tri dia ly"
        profile = profile_from_rows(rows, ["region", "category", "sales", "profit"])
        profile["table_name"] = "generic_sales_orders"
        intent = classify_intent(question)
        linked = link_schema(question, profile, {}, limit=12)
        explicit_plan = build_explicit_aggregate_plan(question, intent, profile, linked)
        answerability = evaluate_answerability(question, intent, profile, {}, {}, explicit_plan)
        evidence_plan = build_evidence_plan(question, intent, profile, {}, linked, explicit_plan)
        evidence_results = [
            {
                "id": "sum_sales_by_region_category",
                "kind": "metric_by_two_dimensions",
                "title": "Sum Sales by Region and Category",
                "status": "ok",
                "metric": "sales",
                "aggregation": "sum",
                "primary_dimension": "region",
                "series_dimension": "category",
                "data": [
                    {"label": "Central", "series": "Furniture", "value": 100, "row_count": 1},
                    {"label": "Central", "series": "Technology", "value": 220, "row_count": 1},
                    {"label": "East", "series": "Furniture", "value": 120, "row_count": 1},
                    {"label": "East", "series": "Technology", "value": 300, "row_count": 1},
                    {"label": "West", "series": "Office Supplies", "value": 260, "row_count": 1},
                    {"label": "West", "series": "Technology", "value": 250, "row_count": 1},
                ],
            },
            {
                "id": "sum_profit_by_region_category",
                "kind": "metric_by_two_dimensions",
                "title": "Sum Profit by Region and Category",
                "status": "ok",
                "metric": "profit",
                "aggregation": "sum",
                "primary_dimension": "region",
                "series_dimension": "category",
                "data": [
                    {"label": "Central", "series": "Furniture", "value": -5, "row_count": 1},
                    {"label": "Central", "series": "Technology", "value": 40, "row_count": 1},
                    {"label": "East", "series": "Furniture", "value": 10, "row_count": 1},
                    {"label": "East", "series": "Technology", "value": 70, "row_count": 1},
                    {"label": "West", "series": "Office Supplies", "value": 85, "row_count": 1},
                    {"label": "West", "series": "Technology", "value": 60, "row_count": 1},
                ],
            },
        ]

        self.assertTrue(explicit_plan["is_explicit"])
        self.assertEqual(answerability["output_type"], "metric_dimension_report")
        self.assertFalse(answerability["should_stop"])
        self.assertTrue(all(item["kind"] == "metric_by_two_dimensions" for item in evidence_plan["items"]))

        spec = build_report_spec(
            question,
            intent,
            profile,
            len(rows),
            answerability=answerability,
            linked_columns=linked,
            evidence_plan=evidence_plan,
            evidence_results=evidence_results,
        )

        self.assertEqual(spec["output_type"], "metric_dimension_report")
        self.assertEqual(spec["candidate_signals"], [])
        self.assertEqual(spec["slicers"], [])
        self.assertEqual([chart["type"] for chart in spec["charts"]], ["stacked_bar", "stacked_bar"])
        self.assertIn("Top Category by Sales is Technology", spec["sections"][0]["content"])
        self.assertIn("Top Category by Profit is Technology", spec["sections"][0]["content"])
        self.assertTrue(any("technology leads sales" in point.lower() for point in spec["executive_points"]))

        path = generate_report(
            question=question,
            sql_query="",
            raw_data=rows,
            columns=["region", "category", "sales", "profit"],
            analytics={"summary": ""},
            report_spec=spec,
            table_profile=profile,
        )
        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("Result Table", html)
        self.assertIn("stacked_bar", html)
        self.assertIn(">Rows<", html)
        self.assertNotIn("Filtered Sample", html)
        self.assertNotIn("Context Required", html)
        self.assertNotIn("<div class=\"block-title\">Candidate Signals</div>", html)

    def test_frequency_distribution_generates_descriptive_observations(self):
        rows = (
            [{"payment_installments": 1} for _ in range(19126)]
            + [{"payment_installments": 2} for _ in range(4320)]
            + [{"payment_installments": 3} for _ in range(3752)]
            + [{"payment_installments": 4} for _ in range(2548)]
            + [{"payment_installments": 5} for _ in range(1948)]
            + [{"payment_installments": 6} for _ in range(1530)]
            + [{"payment_installments": 7} for _ in range(616)]
            + [{"payment_installments": 8} for _ in range(1649)]
            + [{"payment_installments": 9} for _ in range(340)]
            + [{"payment_installments": 10} for _ in range(2390)]
            + [{"payment_installments": 11} for _ in range(7)]
            + [{"payment_installments": 12} for _ in range(49)]
            + [{"payment_installments": 13} for _ in range(5)]
            + [{"payment_installments": 16}]
            + [{"payment_installments": 17}]
            + [{"payment_installments": 24} for _ in range(3)]
        )
        question = "What is the frequency distribution of the number of payment installments?"
        profile = profile_from_rows(rows, ["payment_installments"])
        profile["table_name"] = "payment_transactions"
        intent = classify_intent(question)
        self.assertEqual(intent["intent"], "segmentation")
        linked = link_schema(question, profile, {}, limit=8)
        explicit_plan = build_explicit_aggregate_plan(question, intent, profile, linked)
        evidence_plan = build_evidence_plan(question, intent, profile, {}, linked, explicit_plan)

        self.assertFalse(explicit_plan["is_explicit"])
        self.assertEqual(len(evidence_plan["items"]), 1)
        self.assertEqual(evidence_plan["items"][0]["kind"], "distribution")
        self.assertIn('GROUP BY "payment_installments"', evidence_plan["items"][0]["sql"])
        self.assertIn("ORDER BY label ASC", evidence_plan["items"][0]["sql"])

        evidence_results = [{
            "id": "distribution_payment_installments",
            "kind": "distribution",
            "title": "Frequency Distribution - Payment Installments",
            "success": True,
            "primary_dimension": "payment_installments",
            "data": [
                {"label": 1, "value": 19126},
                {"label": 2, "value": 4320},
                {"label": 3, "value": 3752},
                {"label": 4, "value": 2548},
                {"label": 5, "value": 1948},
                {"label": 6, "value": 1530},
                {"label": 7, "value": 616},
                {"label": 8, "value": 1649},
                {"label": 9, "value": 340},
                {"label": 10, "value": 2390},
                {"label": 11, "value": 7},
                {"label": 12, "value": 49},
                {"label": 13, "value": 5},
                {"label": 16, "value": 1},
                {"label": 17, "value": 1},
                {"label": 24, "value": 3},
            ],
        }]
        observations = build_observations(evidence_results, profile)
        claims = [item["claim"] for item in observations]

        self.assertTrue(any("ranges from 1 to 24" in claim for claim in claims))
        self.assertTrue(any("most frequent" in claim.lower() and "19,126" in claim for claim in claims))
        self.assertTrue(any("4.4x" in claim for claim in claims))
        self.assertTrue(any("Rare" in claim and "16 (1)" in claim for claim in claims))
        self.assertTrue(any("Local frequency spikes" in claim and "8" in claim and "10" in claim for claim in claims))

        spec = build_report_spec(
            question,
            intent,
            profile,
            len(rows),
            linked_columns=linked,
            evidence_plan=evidence_plan,
            evidence_results=evidence_results,
        )

        self.assertEqual(spec["candidate_signals"], [])
        self.assertEqual(spec["slicers"], [])
        self.assertNotIn("Candidate Signals", [card["label"] for card in spec["summary_cards"]])
        self.assertEqual(len(spec["observations"]), 6)
        self.assertTrue(spec["charts"])
        self.assertIn("most frequent value", spec["sections"][0]["content"])
        self.assertTrue(any("Local frequency spikes" in point for point in spec["executive_points"]))

        path = generate_report(
            question=question,
            sql_query="",
            raw_data=rows[:20],
            columns=["payment_installments"],
            analytics={"summary": ""},
            report_spec=spec,
            table_profile=profile,
        )
        html = Path(path).read_text(encoding="utf-8")
        self.assertIn("Key Findings", html)
        self.assertIn("Frequency Distribution - Payment Installments", html)
        self.assertIn("Result Table", html)
        self.assertIn("Local frequency spikes", html)

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

    def test_state_revenue_query_uses_only_requested_state_dimension(self):
        rows = [
            {"region": "Central", "state": "Texas", "sales": 170188, "profit": 10},
            {"region": "West", "state": "California", "sales": 457688, "profit": 20},
            {"region": "East", "state": "New York", "sales": 310876, "profit": 30},
            {"region": "South", "state": "Florida", "sales": 89474, "profit": 40},
        ]
        question = "which state create the most revenue"
        profile = profile_from_rows(rows, ["region", "state", "sales", "profit"])
        profile["table_name"] = "sample_superstore"
        intent = classify_intent(question)
        linked = link_schema(question, profile, {}, limit=12)
        explicit_plan = build_explicit_aggregate_plan(question, intent, profile, linked)
        answerability = evaluate_answerability(question, intent, profile, {}, {}, explicit_plan)
        evidence_plan = build_evidence_plan(question, intent, profile, {}, linked, explicit_plan)

        self.assertTrue(explicit_plan["is_explicit"])
        self.assertEqual([item["column"] for item in explicit_plan["measures"]], ["sales"])
        self.assertEqual([item["column"] for item in explicit_plan["dimensions"]], ["state"])
        self.assertEqual(answerability["output_type"], "metric_dimension_report")
        self.assertEqual(len(evidence_plan["items"]), 1)
        self.assertEqual(evidence_plan["items"][0]["kind"], "metric_by_dimension")
        self.assertIn('GROUP BY "state"', evidence_plan["items"][0]["sql"])
        self.assertNotIn('GROUP BY "region", "state"', evidence_plan["items"][0]["sql"])

        spec = build_report_spec(
            question,
            intent,
            profile,
            len(rows),
            answerability=answerability,
            linked_columns=linked,
            evidence_plan=evidence_plan,
            evidence_results=[
                {
                    "id": "sum_sales_by_state",
                    "kind": "metric_by_dimension",
                    "title": "Sum Sales by State",
                    "status": "ok",
                    "metric": "sales",
                    "aggregation": "sum",
                    "primary_dimension": "state",
                    "data": [
                        {"label": "California", "value": 457688, "row_count": 1},
                        {"label": "New York", "value": 310876, "row_count": 1},
                        {"label": "Texas", "value": 170188, "row_count": 1},
                    ],
                }
            ],
        )

        self.assertEqual([card["note"] for card in spec["summary_cards"] if card["label"] == "Groups"], ["state"])
        self.assertIn("Top State by Sales is California", spec["sections"][0]["content"])
        self.assertEqual([chart["type"] for chart in spec["charts"]], ["bar"])

    def test_total_sales_by_direct_identifier_is_not_blocked_as_outcome(self):
        rows = [
            {"store_id": 1, "department": 1, "weekly_sales": 100.0},
            {"store_id": 1, "department": 2, "weekly_sales": 120.0},
            {"store_id": 1, "department": 3, "weekly_sales": 90.0},
        ]
        question = "Which store has the highest total weekly sales?"
        profile = profile_from_rows(rows, ["store_id", "department", "weekly_sales"])
        profile["table_name"] = "sales"
        intent = classify_intent(question)
        linked = link_schema(question, profile, {}, limit=12)
        explicit_plan = build_explicit_aggregate_plan(question, intent, profile, linked)
        answerability = evaluate_answerability(question, intent, profile, {}, {}, explicit_plan)
        evidence_plan = build_evidence_plan(question, intent, profile, {}, linked, explicit_plan)

        self.assertTrue(explicit_plan["is_explicit"])
        self.assertFalse(answerability["requires_outcome"])
        self.assertFalse(answerability["should_stop"])
        self.assertEqual([item["column"] for item in explicit_plan["measures"]], ["weekly_sales"])
        self.assertEqual([item["column"] for item in explicit_plan["dimensions"]], ["store_id"])
        self.assertIn('GROUP BY "store_id"', evidence_plan["items"][0]["sql"])

    def test_metric_report_blocks_profile_only_fallback(self):
        rows = [
            {"department": 1, "date": "2022-01-01", "weekly_sales": 100.0},
            {"department": 2, "date": "2022-01-01", "weekly_sales": 120.0},
            {"department": 1, "date": "2022-01-08", "weekly_sales": 90.0},
        ]
        question = "Which departments should be compared over time for weekly sales?"
        profile = profile_from_rows(rows, ["department", "date", "weekly_sales"])
        profile["table_name"] = "sales"
        intent = classify_intent(question)
        linked = link_schema(question, profile, {}, limit=12)
        answerability = {
            "output_type": "metric_dimension_report",
            "status": "answerable_from_schema",
            "explicit_aggregate_plan": {
                "is_explicit": True,
                "measures": [{"column": "weekly_sales"}],
                "dimensions": [{"column": "department"}],
            },
        }
        evidence_plan = {
            "items": [
                {
                    "id": "candidate_score",
                    "kind": "candidate_scores",
                    "title": "Question-linked candidate fields",
                    "data": [{"label": "weekly_sales", "value": 100.0}],
                },
                {
                    "id": "summary_weekly_sales",
                    "kind": "numeric_summary",
                    "title": "Summary - Weekly Sales",
                    "data": [{"non_null_count": 3, "avg_value": 103.3}],
                },
            ]
        }
        spec = build_report_spec(
            question,
            intent,
            profile,
            len(rows),
            answerability=answerability,
            linked_columns=linked,
            evidence_plan=evidence_plan,
            evidence_results=evidence_plan["items"],
        )

        self.assertEqual(spec["output_type"], "direct_answer")
        self.assertEqual(spec["answerability"]["status"], "aggregate_evidence_missing")
        self.assertEqual(spec["charts"], [])
        self.assertEqual(spec["slicers"], [])
        self.assertTrue(any("metric report was blocked" in warning for warning in spec["warnings"]))
        validation = {item["stage"]: item["status"] for item in spec["failure_trace"]}
        self.assertEqual(validation["result_validation"], "failed")
        self.assertFalse(any(issue["severity"] == "blocker" for issue in validate_report_spec(spec)))

    def test_unlinked_ranking_does_not_fallback_to_unrelated_sql(self):
        rows = [
            {"region": "Central", "state": "Texas", "segment": "Consumer", "sales": 100, "discount": 0.1},
            {"region": "West", "state": "California", "segment": "Corporate", "sales": 300, "discount": 0.2},
            {"region": "East", "state": "New York", "segment": "Home Office", "sales": 200, "discount": 0.0},
        ]
        question = "which customer group spends the most"
        profile = profile_from_rows(rows, ["region", "state", "segment", "sales", "discount"])
        profile["table_name"] = "sample_superstore"
        intent = classify_intent(question)
        linked = link_schema(question, profile, {}, limit=12)
        explicit_plan = build_explicit_aggregate_plan(question, intent, profile, linked)
        answerability = evaluate_answerability(question, intent, profile, {}, {}, explicit_plan)
        evidence_plan = build_evidence_plan(question, intent, profile, {}, linked, explicit_plan)

        self.assertFalse(explicit_plan["is_explicit"])
        self.assertEqual(answerability["output_type"], "direct_answer")
        self.assertTrue(evidence_plan["warnings"])
        self.assertFalse(any(item.get("sql") for item in evidence_plan["items"]))
        self.assertFalse(any(item.get("id") == "avg_discount_by_segment" for item in evidence_plan["items"]))

        spec = build_report_spec(
            question,
            intent,
            profile,
            len(rows),
            answerability=answerability,
            linked_columns=linked,
            evidence_plan=evidence_plan,
            evidence_results=[],
        )

        self.assertEqual(spec["charts"], [])
        self.assertEqual(spec["slicers"], [])
        self.assertEqual(spec["candidate_signals"], [])
        self.assertIn("cannot safely produce", spec["sections"][0]["content"])


    def test_rebuild_simplified_sql_type_error_downgrades_to_count(self):
        """MAC-SQL: type/cast error → downgrade SUM to COUNT, keep same dimension."""
        item = {
            "kind": "metric_by_dimension",
            "primary_dimension": "group_label",
            "metric": "text_col",
            "aggregation": "sum",
        }
        sql = _rebuild_simplified_sql(item, "ds_table", "operator does not exist: text + integer")
        self.assertIsNotNone(sql)
        self.assertIn('GROUP BY "group_label"', sql)
        self.assertIn("COUNT", sql.upper())

    def test_rebuild_simplified_sql_column_not_found_returns_none(self):
        """MAC-SQL: column-not-found error → skip rebuild (can't fix wrong name)."""
        item = {
            "kind": "metric_by_dimension",
            "primary_dimension": "group_label",
            "metric": "ghost_column",
            "aggregation": "sum",
        }
        result = _rebuild_simplified_sql(item, "ds_table", 'column "ghost_column" does not exist')
        self.assertIsNone(result)

    def test_rebuild_simplified_sql_distribution_fallback(self):
        """MAC-SQL: distribution item → simple GROUP BY count fallback."""
        item = {
            "kind": "distribution",
            "primary_dimension": "type_label",
            "metric": "",
            "aggregation": "count",
        }
        sql = _rebuild_simplified_sql(item, "ds_table", "some transient error")
        self.assertIsNotNone(sql)
        self.assertIn('GROUP BY "type_label"', sql)

    def test_trust_sql_blocks_invalid_column_before_sql_build(self):
        """TRUST-SQL: column that doesn't exist in schema is cleared before SQL generation."""
        rows = [
            {"group_label": "A", "numeric_col": 200},
            {"group_label": "B", "numeric_col": 300},
        ]
        profile = profile_from_rows(rows, ["group_label", "numeric_col"])
        profile["table_name"] = "ds_table"
        intent = classify_intent("total numeric_col by group_label")
        linked = [
            {"column": "ghost_metric", "role": "measure", "name_score": 0.9, "value_score": 0.0, "context_score": 0.0},
            {"column": "group_label", "role": "dimension", "name_score": 0.9, "value_score": 0.0, "context_score": 0.0},
        ]
        with patch("tools.evidence_planner.get_columns_for_table", return_value=["group_label", "numeric_col"]):
            plan = build_evidence_plan(intent=intent, question="total numeric_col by group_label", table_profile=profile,
                                       semantic_context={}, linked_columns=linked)

        # ghost_metric should be blocked → warning emitted
        self.assertTrue(any("ghost_metric" in w for w in plan["warnings"]))
        # No SQL item should reference ghost_metric
        for item in plan["items"]:
            self.assertNotIn("ghost_metric", item.get("sql", ""))

    def test_random_count_ranking_queries_preserve_sql_contract(self):
        """Random schemas: count-ranking must filter mentioned values and rank by count."""
        rng = random.SystemRandom()
        seed = rng.randrange(1, 2**32)
        local = random.Random(seed)

        def word(prefix: str) -> str:
            return prefix + "_" + "".join(local.choice(string.ascii_lowercase) for _ in range(6))

        failures = []
        for _ in range(40):
            group_col = word(local.choice(["band", "level", "bucket", "group"]))
            filter_col = word(local.choice(["status", "class", "flag", "result"]))
            noise_col = word("noise")
            positive = local.choice(["Approved", "Completed", "Selected", "Matched", "Active"]) + str(local.randrange(10, 99))
            negative = local.choice(["Rejected", "Pending", "Skipped", "Inactive"]) + str(local.randrange(10, 99))
            group_values = list(range(1, local.randrange(5, 14))) if local.choice([True, False]) else [
                word("g") for _ in range(local.randrange(4, 9))
            ]
            rows = []
            for index in range(local.randrange(80, 180)):
                rows.append({
                    group_col: local.choice(group_values),
                    filter_col: positive if local.random() < local.uniform(0.25, 0.75) else negative,
                    noise_col: local.randrange(1, 1000),
                })
            profile = profile_from_rows(rows, [group_col, filter_col, noise_col])
            profile["table_name"] = word("table")
            question = local.choice([
                f"Which {group_col.replace('_', ' ')} has the highest number of rows with {positive}?",
                f"What {group_col.replace('_', ' ')} has the most records where {filter_col.replace('_', ' ')} is {positive}?",
                f"Rank {group_col.replace('_', ' ')} by number of records that are {positive}.",
            ])
            intent = classify_intent(question)
            linked = link_schema(question, profile, {}, limit=12)
            explicit_plan = build_explicit_aggregate_plan(question, intent, profile, linked)
            evidence_plan = build_evidence_plan(question, intent, profile, {}, linked, explicit_plan)
            sql_items = [item for item in evidence_plan["items"] if item.get("sql")]
            sql = sql_items[0]["sql"] if sql_items else ""

            expected_counts = {}
            for row in rows:
                if row[filter_col] == positive:
                    expected_counts[row[group_col]] = expected_counts.get(row[group_col], 0) + 1
            expected_top = max(expected_counts.items(), key=lambda item: item[1])[0]

            problems = []
            if not explicit_plan.get("is_explicit"):
                problems.append(f"not explicit: {explicit_plan.get('reason')}")
            if explicit_plan.get("aggregation") != "count":
                problems.append(f"aggregation={explicit_plan.get('aggregation')}")
            if [item.get("column") for item in explicit_plan.get("dimensions", [])[:1]] != [group_col]:
                problems.append(f"dimension={explicit_plan.get('dimensions')} expected={group_col}")
            if not any(item.get("column") == filter_col and item.get("value") == positive for item in explicit_plan.get("filters", [])):
                problems.append(f"filters={explicit_plan.get('filters')} expected={filter_col}={positive}")
            if f'GROUP BY "{group_col}"' not in sql:
                problems.append(f"missing group by in sql={sql}")
            if f'"{filter_col}" = \'{positive}\'' not in sql:
                problems.append(f"missing filter in sql={sql}")
            if "ORDER BY value DESC" not in sql:
                problems.append(f"missing rank ordering in sql={sql}")
            if "ORDER BY label ASC" in sql:
                problems.append(f"label ordering leaked into ranking sql={sql}")
            if str(expected_top) not in {str(value) for value in group_values}:
                problems.append("test generator produced impossible expected top")
            if problems:
                failures.append({
                    "seed": seed,
                    "question": question,
                    "group_col": group_col,
                    "filter_col": filter_col,
                    "positive": positive,
                    "problems": problems,
                    "sql": sql,
                    "linked": linked,
                })
                break

        self.assertEqual(failures, [], f"Random count-ranking contract failed: {json.dumps(failures, indent=2)}")


if __name__ == "__main__":
    unittest.main()
