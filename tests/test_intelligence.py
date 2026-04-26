import os
import tempfile
import unittest
import uuid


os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(prefix="geocc-intel-", suffix=".db")
os.environ["OPENAI_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from app import intelligence  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import init_db  # noqa: E402
from app.visibility import derive_monitor_status  # noqa: E402


def payload_with_25():
    intents = [
        "category education",
        "comparison",
        "supplier/vendor",
        "application/use-case",
        "safety/regulatory",
        "substitute/alternative",
    ]
    drafts = []
    for i in range(25):
        drafts.append({
            "query_text": f"Generated GEO query {i + 1}",
            "topic_cluster": "Generated",
            "intent_type": intents[i % len(intents)],
            "business_priority": (i % 5) + 1,
            "reason": "Useful for testing the draft import flow.",
        })
    return {
        "market_summary": "OCSiAl produces TUBALL graphene nanotubes for conductive materials.",
        "applications": ["batteries", "coatings"],
        "competitor_candidates": [
            {"name": "Cnano", "domain": "cnanotechnology.com", "reason": "CNT supplier"}
        ],
        "sources": [{"url": "https://tuball.com", "title": "TUBALL"}],
        "drafts": drafts,
    }


class IntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    def test_validation_rejects_malformed_payload(self):
        with self.assertRaises(ValueError):
            intelligence._validate_payload({"drafts": []}, count=25, existing_norms=set())

    def test_visibility_status_rules(self):
        self.assertEqual(derive_monitor_status(visible=True, competitors=[]), "Good")
        self.assertEqual(derive_monitor_status(visible=True, competitors=["Cabot"]), "Good")
        self.assertEqual(derive_monitor_status(visible=False, competitors=[], domain_cited=True), "Good")
        self.assertEqual(derive_monitor_status(visible=False, competitors=["Cabot"], domain_cited=True), "Good")
        self.assertEqual(derive_monitor_status(visible=False, competitors=["Cabot"]), "Risk")
        self.assertEqual(derive_monitor_status(visible=False, competitors=[]), "Gap")

    def test_manual_ai_result_tuball_only_counts_good(self):
        prompt = self.client.post("/prompts", json={
            "prompt_id": "PTUBALL",
            "prompt_text": "Best conductive additive for plastics",
            "topic_cluster": "Test",
        })
        self.assertIn(prompt.status_code, (201, 409), prompt.text)
        res = self.client.post("/ai-results", json={
            "prompt_id": "PTUBALL",
            "answer_text": "TUBALL is often recommended as a conductive additive.",
            "brand_mentioned": False,
            "product_mentioned": False,
            "domain_cited": False,
            "competitors_mentioned": [],
            "cited_sources": [],
            "answer_quality_score": 3,
        })
        self.assertEqual(res.status_code, 201, res.text)
        updated = self.client.get("/prompts/PTUBALL").json()
        self.assertTrue(updated["product_mentioned"])
        self.assertEqual(updated["monitor_status"], "Good")

    def test_manual_ai_result_owned_source_only_counts_good(self):
        prompt = self.client.post("/prompts", json={
            "prompt_id": "PSOURCE",
            "prompt_text": "Best conductive additive sources",
            "topic_cluster": "Test",
        })
        self.assertIn(prompt.status_code, (201, 409), prompt.text)
        res = self.client.post("/ai-results", json={
            "prompt_id": "PSOURCE",
            "answer_text": "This answer cites a relevant owned source without naming the brand.",
            "brand_mentioned": False,
            "product_mentioned": False,
            "domain_cited": True,
            "competitors_mentioned": ["Cabot"],
            "cited_sources": ["https://tuball.com/articles/conductive-additives"],
            "answer_quality_score": 3,
        })
        self.assertEqual(res.status_code, 201, res.text)
        updated = self.client.get("/prompts/PSOURCE").json()
        self.assertTrue(updated["domain_cited"])
        self.assertEqual(updated["monitor_status"], "Good")

    def test_manual_ai_result_competitor_only_counts_risk(self):
        prompt = self.client.post("/prompts", json={
            "prompt_id": "PRISK",
            "prompt_text": "Conductive carbon black suppliers",
            "topic_cluster": "Test",
        })
        self.assertIn(prompt.status_code, (201, 409), prompt.text)
        res = self.client.post("/ai-results", json={
            "prompt_id": "PRISK",
            "answer_text": "Cabot and Orion are common suppliers in this space.",
            "brand_mentioned": False,
            "product_mentioned": False,
            "domain_cited": False,
            "competitors_mentioned": ["Cabot", "Orion"],
            "cited_sources": [],
            "answer_quality_score": 3,
        })
        self.assertEqual(res.status_code, 201, res.text)
        updated = self.client.get("/prompts/PRISK").json()
        self.assertEqual(updated["monitor_status"], "Risk")

    def test_dashboard_excludes_unchecked_from_visibility(self):
        self.client.post("/prompts", json={
            "prompt_id": "PUNCHECKED",
            "prompt_text": "Unchecked dashboard prompt",
            "topic_cluster": "Test",
        })
        dash = self.client.get("/dashboard")
        self.assertEqual(dash.status_code, 200, dash.text)
        data = dash.json()
        self.assertIn("imported_prompts", data)
        self.assertIn("run_coverage", data)
        self.assertGreaterEqual(data["imported_prompts"]["value"], 1)
        self.assertLess(data["run_coverage"]["value"], 100)

    def test_recommendation_processor_uses_gap_risk_only(self):
        suffix = uuid.uuid4().hex[:8]
        unchecked_id = f"PUN-{suffix}"
        risk_id = f"PRS-{suffix}"
        gap_id = f"PGP-{suffix}"
        for prompt_id, text in [
            (unchecked_id, "Unchecked processor prompt"),
            (risk_id, "Conductive additive suppliers for plastics"),
            (gap_id, "Best antistatic additive for elastomers"),
        ]:
            res = self.client.post("/prompts", json={
                "prompt_id": prompt_id,
                "prompt_text": text,
                "topic_cluster": "Processor Test",
                "business_priority": 5,
            })
            self.assertIn(res.status_code, (201, 409), res.text)

        self.client.post("/ai-results", json={
            "prompt_id": risk_id,
            "answer_text": "Cabot and Orion are common conductive additive suppliers.",
            "competitors_mentioned": ["Cabot", "Orion"],
            "answer_quality_score": 3,
        })
        self.client.post("/ai-results", json={
            "prompt_id": gap_id,
            "answer_text": "Several additive chemistries can reduce static in elastomers.",
            "answer_quality_score": 3,
        })

        res = self.client.post("/recommendations/process-prompts")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertGreaterEqual(data["considered_prompts"], 2)
        rec = next((r for r in data["recommendations"] if r["score_breakdown"].get("cluster") == "Processor Test"), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["score_breakdown"]["scope"], "cluster")
        self.assertEqual(rec["score_breakdown"]["source"], "prompt_evidence")
        self.assertEqual(rec["score_breakdown"]["prompt_count"], 2)

        summary = self.client.get("/recommendations/summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertIn("cluster_level", summary.json())

    def test_validation_rejects_branded_repetitive_drafts_and_falls_back(self):
        payload = {
            "market_summary": "OCSiAl produces TUBALL graphene nanotubes for conductive materials.",
            "applications": [],
            "competitor_candidates": [],
            "sources": [],
            "drafts": [
                {
                    "query_text": f"How do TUBALL nanotubes improve conductivity in material {i}?",
                    "topic_cluster": "Bad branded",
                    "intent_type": "application/use-case",
                    "business_priority": 1,
                    "reason": "Too branded.",
                }
                for i in range(25)
            ],
        }
        cleaned = intelligence._validate_payload(payload, count=25, existing_norms=set())
        self.assertEqual(len(cleaned["drafts"]), 25)
        self.assertTrue(all("tuball" not in d["query_text"].lower() for d in cleaned["drafts"]))
        self.assertTrue(all(d["business_priority"] >= 3 for d in cleaned["drafts"]))
        self.assertEqual(cleaned["drafts"][0]["query_text"], "What is the best conductive additive for polymers?")

    def test_validation_repairs_missing_required_intent(self):
        payload = payload_with_25()
        for item in payload["drafts"]:
            if item["intent_type"] == "substitute/alternative":
                item["intent_type"] = "category education"
        cleaned = intelligence._validate_payload(payload, count=25, existing_norms=set())
        intents = {d["intent_type"] for d in cleaned["drafts"]}
        self.assertIn("substitute/alternative", intents)
        self.assertEqual(len(cleaned["drafts"]), 25)

    def test_validation_filters_weak_how_do_prompts(self):
        payload = payload_with_25()
        payload["drafts"][0]["query_text"] = "How do carbon nanotubes improve mechanical properties?"
        cleaned = intelligence._validate_payload(payload, count=25, existing_norms=set())
        texts = [d["query_text"] for d in cleaned["drafts"]]
        self.assertNotIn("How do carbon nanotubes improve mechanical properties?", texts)

    def test_generation_creates_drafts_not_prompts(self):
        before = len(self.client.get("/prompts").json())
        original = intelligence._call_responses_api
        intelligence._call_responses_api = lambda db, count: payload_with_25()
        try:
            res = self.client.post("/intelligence/generate-drafts", json={"count": 25})
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(len(data["drafts"]), 25)
            self.assertEqual(len(self.client.get("/prompts").json()), before)
        finally:
            intelligence._call_responses_api = original

    def test_approval_imports_once_and_skips_duplicates(self):
        original = intelligence._call_responses_api
        intelligence._call_responses_api = lambda db, count: payload_with_25()
        try:
            batch = self.client.post("/intelligence/generate-drafts", json={"count": 25}).json()
        finally:
            intelligence._call_responses_api = original

        ids = [d["draft_id"] for d in batch["drafts"][:2]]
        first = self.client.post(f"/intelligence/drafts/{batch['batch_id']}/approve", json={"draft_ids": ids})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(first.json()["imported"]), 2)

        second = self.client.post(f"/intelligence/drafts/{batch['batch_id']}/approve", json={"draft_ids": ids})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(second.json()["imported"]), 0)
        self.assertEqual(len(second.json()["skipped"]), 2)


if __name__ == "__main__":
    unittest.main()
