import os
import tempfile
import unittest
import uuid
from datetime import date


os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(prefix="geocc-intel-", suffix=".db")
os.environ["OPENAI_API_KEY"] = "test-key"

from fastapi.testclient import TestClient  # noqa: E402

from app import intelligence  # noqa: E402
from app import models  # noqa: E402
from app import prompt_research  # noqa: E402
from app.traffic import classify_ai_source  # noqa: E402
from app.database import SessionLocal  # noqa: E402
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

    def test_prompt_research_run_and_apply_reviewed_changes(self):
        suffix = uuid.uuid4().hex[:8]
        delete_id = f"PDEL-{suffix}"
        self.client.post("/prompts", json={
            "prompt_id": delete_id,
            "prompt_text": f"obsolete low evidence prompt {suffix}",
            "topic_cluster": "Low value",
            "business_priority": 1,
        })
        db = SessionLocal()
        try:
            before = db.query(models.Prompt).count()
            db.add(models.GoogleSearchMetric(
                metric_id=f"GSC-{suffix}",
                site_url="https://tuball.com/",
                date_start=date.today(),
                date_end=date.today(),
                query=f"best conductive additive for polymers {suffix}",
                page="https://tuball.com/articles/conductive-additives",
                impressions=2400,
                clicks=24,
                avg_position=8.0,
            ))
            db.add(models.GoogleAnalyticsMetric(
                metric_id=f"GA4-{suffix}",
                property_id="test",
                date_start=date.today(),
                date_end=date.today(),
                page_path="/articles/conductive-additives",
                page_title="Conductive additives",
                sessions=320,
                active_users=240,
            ))
            db.commit()
        finally:
            db.close()

        run = self.client.post("/prompt-research/run", json={"count": 25})
        self.assertEqual(run.status_code, 200, run.text)
        data = run.json()
        self.assertEqual(len(data["items"]), 25)
        self.assertEqual(data["batch"]["source_status"]["gsc"], "ok")
        db = SessionLocal()
        try:
            self.assertEqual(db.query(models.Prompt).count(), before)
        finally:
            db.close()

        add_item = next(i for i in data["items"] if i["action"] == "Add")
        delete_item = next((i for i in data["items"] if i["action"] == "Delete" and i["prompt_id"] == delete_id), None)
        if delete_item is None:
            db = SessionLocal()
            try:
                manual_delete = models.PromptResearchItem(
                    item_id=f"PRI-{suffix}",
                    batch_id=data["batch"]["batch_id"],
                    action="Delete",
                    prompt_id=delete_id,
                    query_text=f"obsolete low evidence prompt {suffix}",
                    topic_cluster="Low value",
                    intent_type="category education",
                    priority_score=25,
                    confidence_score=40,
                    evidence={"monitor": {"status": "Unchecked"}},
                    reason="Low evidence and low priority; remove to keep monitoring focused.",
                    status="draft",
                )
                db.add(manual_delete)
                db.commit()
                delete_item = {"item_id": manual_delete.item_id}
            finally:
                db.close()
        applied = self.client.post(f"/prompt-research/{data['batch']['batch_id']}/apply", json={
            "item_ids": [add_item["item_id"], delete_item["item_id"]],
        })
        self.assertEqual(applied.status_code, 200, applied.text)
        body = applied.json()
        self.assertEqual(len(body["added"]), 1)
        self.assertIn(delete_id, body["deleted"])
        self.assertEqual(body["added"][0]["monitor_status"], "Unchecked")
        self.assertEqual(self.client.get(f"/prompts/{delete_id}").status_code, 404)

    def test_prompt_research_coverage_finds_missing_application_and_supplier(self):
        db = SessionLocal()
        try:
            coverage = prompt_research.build_coverage_map(db, gsc_rows=[], ga4_rows=[], prompts=[], trend_rows=[])
        finally:
            db.close()
        add_candidates = [r for r in coverage if r["monitor_status"] == "missing"]
        prompts = [r["representative_prompt"].lower() for r in add_candidates]
        self.assertTrue(any("conductive silicone rubber" in p for p in prompts))
        self.assertTrue(any("single-walled carbon nanotube" in p and "supply" in p for p in prompts))

    def test_prompt_research_existing_equivalent_prevents_missing_add(self):
        p = models.Prompt(
            prompt_id="PEQ",
            prompt_text="What additive should I use for electrically conductive silicone rubber?",
            topic_cluster="Rubber and elastomers",
            monitor_status="Unchecked",
        )
        db = SessionLocal()
        try:
            coverage = prompt_research.build_coverage_map(db, gsc_rows=[], ga4_rows=[], prompts=[p], trend_rows=[])
        finally:
            db.close()
        silicone = next(r for r in coverage if r["coverage_topic"] == "electrically conductive silicone rubber additive")
        self.assertNotEqual(silicone["monitor_status"], "missing")

    def test_prompt_research_low_value_duplicate_produces_delete(self):
        low = models.Prompt(
            prompt_id="PDUP-LOW",
            prompt_text="duplicate conductive additive prompt",
            topic_cluster="Test",
            monitor_status="Gap",
            business_priority=1,
        )
        better = models.Prompt(
            prompt_id="PDUP-HIGH",
            prompt_text="duplicate conductive additive prompt",
            topic_cluster="Test",
            monitor_status="Good",
            business_priority=5,
        )
        rows = prompt_research._delete_candidates([], [], [], [low, better])
        self.assertTrue(any(r["prompt_id"] == "PDUP-LOW" and r["action"] == "Delete" for r in rows))

    def test_prompt_research_does_not_delete_correct_small_queue(self):
        prompts = [
            models.Prompt(
                prompt_id=f"PKEEP-{i}",
                prompt_text=text,
                topic_cluster="Supplier / procurement",
                monitor_status="Good",
                business_priority=4,
            )
            for i, text in enumerate([
                "Which companies supply single-walled carbon nanotube additives?",
                "Which suppliers are recommended for conductive additives in polymer compounds?",
                "What additive should I use for electrically conductive silicone rubber?",
            ])
        ]
        rows = prompt_research._delete_candidates([], [], [], prompts)
        self.assertEqual(rows, [])

    def test_prompt_research_delete_reason_for_outside_business_scope(self):
        prompt = models.Prompt(
            prompt_id="POUT",
            prompt_text="Where to order ESD floors?",
            topic_cluster="Noise",
            monitor_status="Unchecked",
            business_priority=1,
        )
        rows = prompt_research._delete_candidates([], [], [], [prompt])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evidence"]["coverage"]["monitor_status"], "outside-business-scope")
        self.assertIn("finished downstream product", rows[0]["reason"])

    def test_prompt_research_caps_duplicate_intent_groups(self):
        prompts = [
            models.Prompt(
                prompt_id=f"PSUP-{i}",
                prompt_text=f"Which companies supply single-walled carbon nanotube additives? variant {i}",
                topic_cluster="Supplier / procurement",
                monitor_status="Unchecked",
                business_priority=1,
            )
            for i in range(7)
        ]
        rows = prompt_research._delete_candidates([], [], [], prompts)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["evidence"]["coverage"]["monitor_status"] == "duplicate-intent" for r in rows))

    def test_prompt_research_ranks_add_coverage_before_queue_cleanup(self):
        selected = prompt_research._rank_and_balance([
            {
                "action": "Delete",
                "prompt_id": "PD1",
                "query_text": "low value monitored prompt",
                "priority_score": 55,
            },
            {
                "action": "Add",
                "query_text": "What additive should I use for electrically conductive silicone rubber?",
                "priority_score": 42,
            },
        ], 2)
        self.assertEqual(selected[0]["action"], "Add")

    def test_prompt_research_approve_add_runs_monitor_and_reject_does_not_mutate(self):
        suffix = uuid.uuid4().hex[:8]
        original = prompt_research.monitor_engine.run_query
        prompt_research.monitor_engine.run_query = lambda db, prompt: {"prompt_id": prompt.prompt_id, "monitor_status": "Good", "cost_usd": 0}
        try:
            run = self.client.post("/prompt-research/run", json={"count": 10})
            self.assertEqual(run.status_code, 200, run.text)
            data = run.json()
            add_item = next(i for i in data["items"] if i["action"] == "Add")
            approve = self.client.post(f"/prompt-research/{data['batch']['batch_id']}/items/{add_item['item_id']}/approve")
            self.assertEqual(approve.status_code, 200, approve.text)
            body = approve.json()
            self.assertIsNotNone(body["added"])
            self.assertEqual(body["monitor"]["monitor_status"], "Good")

            before = len(self.client.get("/prompts").json())
            db = SessionLocal()
            try:
                reject = models.PromptResearchItem(
                    item_id=f"PRI-REJ-{suffix}",
                    batch_id=data["batch"]["batch_id"],
                    action="Add",
                    query_text=f"Rejected prompt {suffix}",
                    topic_cluster="Test",
                    intent_type="category education",
                    priority_score=50,
                    confidence_score=50,
                    evidence={},
                    reason="test",
                    status="draft",
                )
                db.add(reject)
                db.commit()
                reject_id = reject.item_id
            finally:
                db.close()
            rejected = self.client.post(f"/prompt-research/{data['batch']['batch_id']}/items/{reject_id}/reject")
            self.assertEqual(rejected.status_code, 200, rejected.text)
            after = len(self.client.get("/prompts").json())
            self.assertEqual(after, before)
        finally:
            prompt_research.monitor_engine.run_query = original

    def test_ai_traffic_classifier_and_monthly_endpoint(self):
        self.assertEqual(classify_ai_source("chatgpt.com", "referral"), "ChatGPT")
        self.assertEqual(classify_ai_source("perplexity.ai", "referral"), "Perplexity")
        self.assertEqual(classify_ai_source("claude.ai", "referral"), "Claude")
        self.assertEqual(classify_ai_source("gemini.google.com", "referral"), "Gemini")
        self.assertEqual(classify_ai_source("deepseek.com", "referral"), "DeepSeek")
        suffix = uuid.uuid4().hex[:8]
        db = SessionLocal()
        try:
            db.add(models.AiTrafficMetric(
                metric_id=f"AIT-{suffix}",
                source="ChatGPT",
                source_detail="chatgpt.com",
                date_start=date.today(),
                date_end=date.today(),
                sessions=12,
                active_users=9,
                conversions=1.0,
                landing_pages=[{"page": "/articles/test", "sessions": 12, "users": 9, "conversions": 1.0}],
            ))
            db.commit()
        finally:
            db.close()
        res = self.client.get("/traffic/ai/monthly")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertGreaterEqual(data["total_sessions"], 12)
        self.assertTrue(any(row["source"] == "ChatGPT" for row in data["source_breakdown"]))

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
        db = SessionLocal()
        try:
            db.add(models.GoogleSearchMetric(
                metric_id=f"GSC-PROC-{suffix}",
                site_url="https://tuball.com/",
                date_start=date.today(),
                date_end=date.today(),
                query="conductive additive suppliers plastics antistatic elastomers",
                page="https://tuball.com/articles/conductive-additives",
                clicks=35,
                impressions=3500,
                avg_position=9.0,
            ))
            db.add(models.GoogleAnalyticsMetric(
                metric_id=f"GA4-PROC-{suffix}",
                property_id="test",
                date_start=date.today(),
                date_end=date.today(),
                page_path="/articles/conductive-additives",
                page_title="Conductive additive suppliers for plastics and elastomers",
                active_users=250,
                sessions=310,
            ))
            db.commit()
        finally:
            db.close()

        res = self.client.post("/recommendations/process-prompts")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertGreaterEqual(data["considered_prompts"], 2)
        rec = next((
            r for r in data["recommendations"]
            if r["score_breakdown"].get("cluster") == "Processor Test"
            or "Processor Test" in (r["score_breakdown"].get("source_clusters") or [])
        ), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["score_breakdown"]["scope"], "cluster")
        self.assertEqual(rec["score_breakdown"]["source"], "cluster_evidence")
        self.assertGreaterEqual(rec["score_breakdown"]["prompt_count"], 2)
        self.assertIn(risk_id, rec["score_breakdown"]["linked_prompt_ids"])
        self.assertIn(gap_id, rec["score_breakdown"]["linked_prompt_ids"])
        self.assertNotIn(unchecked_id, rec["score_breakdown"]["linked_prompt_ids"])
        self.assertIn(rec["type"], {
            "Add Comparison Section",
            "Create Source Page",
            "Add FAQ / Buyer Questions",
            "Add Citation Proof",
            "Upgrade Existing Page",
        })

        summary = self.client.get("/recommendations/summary")
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertIn("cluster_level", summary.json())

    def test_recommendation_type_comparison_when_competitor_pressure_high(self):
        suffix = uuid.uuid4().hex[:8]
        ids = [f"PCOMP-{suffix}-{i}" for i in range(3)]
        for prompt_id in ids:
            self.client.post("/prompts", json={
                "prompt_id": prompt_id,
                "prompt_text": "Best conductive additive supplier",
                "topic_cluster": f"Comparison {suffix}",
                "business_priority": 5,
            })
            self.client.post("/ai-results", json={
                "prompt_id": prompt_id,
                "answer_text": "Cabot, Orion, and Nanocyl are often compared.",
                "competitors_mentioned": ["Cabot", "Orion", "Nanocyl"],
                "cited_sources": ["https://cabotcorp.com"],
                "answer_quality_score": 3,
            })
        data = self.client.post("/recommendations/process-prompts").json()
        rec = next(r for r in data["recommendations"] if r["score_breakdown"].get("cluster") == f"Comparison {suffix}")
        self.assertEqual(rec["type"], "Add Comparison Section")
        self.assertEqual(rec["score_breakdown"]["opportunity_type"], "Add Comparison Section")

    def test_recommendation_type_upgrade_existing_page_with_gsc_ga4_leverage(self):
        suffix = uuid.uuid4().hex[:8]
        cluster = f"Leverage {suffix}"
        prompt_id = f"PLEV-{suffix}"
        self.client.post("/prompts", json={
            "prompt_id": prompt_id,
            "prompt_text": "conductive additive for polymer compounds",
            "topic_cluster": cluster,
            "business_priority": 5,
        })
        self.client.post("/ai-results", json={
            "prompt_id": prompt_id,
            "answer_text": "Several suppliers exist but the answer does not name the target brand.",
            "answer_quality_score": 3,
        })
        db = SessionLocal()
        try:
            db.add(models.GoogleSearchMetric(
                metric_id=f"GSC-{suffix}",
                site_url="https://tuball.com/",
                date_start=date.today(),
                date_end=date.today(),
                query="conductive additive polymer compounds",
                page="https://tuball.com/polymer-conductive-additives",
                clicks=40,
                impressions=4000,
                avg_position=9.5,
            ))
            db.add(models.GoogleAnalyticsMetric(
                metric_id=f"GA4-{suffix}",
                property_id="381976460",
                date_start=date.today(),
                date_end=date.today(),
                page_path="/polymer-conductive-additives",
                page_title="Conductive additives for polymer compounds",
                active_users=500,
                sessions=650,
            ))
            db.commit()
        finally:
            db.close()
        data = self.client.post("/recommendations/process-prompts").json()
        rec = next(r for r in data["recommendations"] if r["score_breakdown"].get("cluster") == cluster)
        self.assertEqual(rec["type"], "Upgrade Existing Page")
        self.assertTrue(rec["score_breakdown"]["target_pages"])

    def test_evidence_clusters_endpoint_returns_canonical_model(self):
        suffix = uuid.uuid4().hex[:8]
        cluster = f"Evidence {suffix}"
        prompt_id = f"PEV-{suffix}"
        self.client.post("/prompts", json={
            "prompt_id": prompt_id,
            "prompt_text": "best conductive additive for coatings",
            "topic_cluster": cluster,
        })
        self.client.post("/ai-results", json={
            "prompt_id": prompt_id,
            "answer_text": "Cabot is often cited for conductive coatings.",
            "competitors_mentioned": ["Cabot"],
            "cited_sources": ["https://cabotcorp.com"],
            "answer_quality_score": 3,
        })
        res = self.client.get("/evidence/clusters")
        self.assertEqual(res.status_code, 200, res.text)
        row = next((r for r in res.json() if r["cluster"] == cluster), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["risk_count"], 1)
        self.assertEqual(row["competitor_pressure_rate"], 100)
        self.assertIn("opportunity_type", row)

    def test_recommendation_done_stores_lifecycle_metadata(self):
        suffix = uuid.uuid4().hex[:8]
        prompt_id = f"PDONE-{suffix}"
        self.client.post("/prompts", json={
            "prompt_id": prompt_id,
            "prompt_text": "best antistatic additive compared with carbon black",
            "topic_cluster": f"Done {suffix}",
            "business_priority": 5,
        })
        self.client.post("/ai-results", json={
            "prompt_id": prompt_id,
            "answer_text": "Cabot carbon black is commonly recommended; OCSiAl and TUBALL are not mentioned.",
            "competitors_mentioned": ["Cabot"],
            "answer_quality_score": 3,
        })
        data = self.client.post("/recommendations/process-prompts").json()
        rec = data["recommendations"][0]
        done = self.client.patch(f"/recommendations/{rec['recommendation_id']}/status", json={
            "status": "Done",
            "notes": "Published page update.",
            "affected_page_url": "https://tuball.com/test-page",
            "expected_prompt_ids": [prompt_id],
        })
        self.assertEqual(done.status_code, 200, done.text)
        meta = done.json()["score_breakdown"]
        self.assertEqual(done.json()["status"], "Done")
        self.assertEqual(meta["lifecycle"]["notes"], "Published page update.")
        self.assertIn("completed_at", meta["lifecycle"])

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
