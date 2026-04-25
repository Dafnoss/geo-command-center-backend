import os
import tempfile
import unittest


os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(prefix="geocc-intel-", suffix=".db")
os.environ["OPENAI_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from app import intelligence  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import init_db  # noqa: E402


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
