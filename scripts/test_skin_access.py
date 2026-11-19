"""Exercise real meeting/admin auth and quotas with isolated storage and mocked AI."""
import copy
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from livekit import api
from PIL import Image
from backend import analytics, admin_accounts, skin_routes, skin_access
from backend import local_skin_rag_routes as analysis
from backend import medicine_upload_routes as uploads
from backend import participant_documents as documents


class SkinAccessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.start_patch(patch.object(analytics, "DB_PATH", Path(directory.name) / "test.sqlite3"))
        self.start_patch(patch.dict(os.environ, {
            "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-secret-for-isolated-test-only",
            "AGENT_EVENT_KEY": "test-agent-event-signing-key-32-characters", "ADMIN_EMAIL": "admin@example.test",
            "ADMIN_PASSWORD_HASH": "unused-in-token-tests", "ADMIN_TOKEN_SECRET": "a" * 40,
            "SKIN_PARTICIPANT_LIMIT_10M": "3", "SKIN_MEETING_LIMIT_1H": "20",
            "SKIN_CONCURRENT_LIMIT": "1", "MATEO_MEDICINE_DEV_SEARCH_ENABLED": "false",
        }))
        analytics.initialize_db()
        admin_accounts.initialize_admin_db()
        analytics.save_meeting(dict(meeting_id="meeting", room_name="room", invite_code="invite",
                                    owner_token="owner", created_at=time.time(), dispatch_id=None, state="created"))
        analytics.add_participant("meeting", "human_test", "Tester")
        self.connected = self.start_patch(patch.object(skin_routes, "verify_connected", new_callable=AsyncMock))
        self.start_patch(patch.object(documents, "verify_connected", self.connected))
        self.inference = self.start_patch(patch.object(analysis, "analyze_skin_and_retrieve"))
        result = dict(success=True, skin_observations=dict(face_visible=True, image_usable=True,
                      visible_features=["redness"], quality_note="clear"), medicine_rag={})
        self.inference.side_effect = lambda _, **kwargs: copy.deepcopy(result)
        self.store = self.start_patch(patch.object(uploads, "save_and_index", return_value={"success": True}))
        app = FastAPI()
        app.include_router(analysis.router)
        app.include_router(uploads.router)
        app.include_router(documents.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        image = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(image, format="JPEG")
        self.image = image.getvalue()

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def token(self, room="room", identity="human_test"):
        return (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
                .with_identity(identity).with_grants(api.VideoGrants(room_join=True, room=room)).to_jwt())

    def analyze(self, token=None, **form):
        data = dict(meeting_id="meeting", consent="true", processing_consent="true")
        data.update(form)
        return self.client.post("/api/v1/skin/local-rag", data=data,
            files={"image": ("snapshot.jpg", self.image, "image/jpeg")},
            headers={"Authorization": f"Bearer {token or self.token()}"})

    def test_participant_can_analyze_without_dev_token_or_localhost_access(self):
        response = self.analyze()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("voice_grant", response.json())
        self.connected.assert_awaited_once_with("room", "human_test")

    def test_invalid_and_wrong_room_tokens_are_rejected_before_inference(self):
        self.assertEqual(self.analyze(token="invalid").status_code, 401)
        self.assertEqual(self.analyze(token=self.token(room="other")).status_code, 403)
        self.assertEqual(self.analyze(token=self.token(identity="human_unknown")).status_code, 403)
        self.inference.assert_not_called()

    def test_disconnected_participant_is_rejected(self):
        self.connected.side_effect = HTTPException(403, "Must be connected")
        self.assertEqual(self.analyze().status_code, 403)
        self.inference.assert_not_called()

    def test_both_consents_are_required(self):
        self.assertEqual(self.analyze(consent="false").status_code, 400)
        self.assertEqual(self.analyze(processing_consent="false").status_code, 400)
        self.inference.assert_not_called()

    def test_participant_quota_survives_completed_requests(self):
        for _ in range(3):
            self.assertEqual(self.analyze().status_code, 200)
        response = self.analyze()
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)
        self.assertEqual(self.inference.call_count, 3)

    def test_busy_server_rejects_without_starting_another_analysis(self):
        request_id = skin_access.reserve_analysis("meeting", "human_test")
        self.assertEqual(self.analyze().status_code, 429)
        self.inference.assert_not_called()
        skin_access.finish_analysis(request_id)
        self.assertEqual(self.analyze().status_code, 200)

    def test_meeting_quota_cannot_be_bypassed_by_another_participant(self):
        with patch.dict(os.environ, {"SKIN_MEETING_LIMIT_1H": "1"}):
            self.assertEqual(self.analyze().status_code, 200)
            analytics.add_participant("meeting", "human_second", "Second")
            self.assertEqual(self.analyze(token=self.token(identity="human_second")).status_code, 429)

    def test_failed_inference_releases_concurrency_reservation(self):
        self.inference.side_effect = ValueError("invalid camera frame")
        self.assertEqual(self.analyze().status_code, 422)
        with analytics.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM skin_analysis_requests WHERE finished_at IS NULL").fetchone()[0], 0)

    def test_upload_requires_active_admin_and_rejects_participant_and_dev_tokens(self):
        files = {"file": ("reference.txt", b"Reference text", "text/plain")}
        for headers in ({}, {"Authorization": f"Bearer {self.token()}"}, {"X-Dev-Token": "old-dev-token"}):
            self.assertEqual(self.client.post("/api/v1/medicine/upload", files=files, headers=headers).status_code, 401)
        self.store.assert_not_called()
        with analytics.connect() as db:
            account = db.execute("SELECT * FROM admin_accounts").fetchone()
        admin_token = admin_accounts.create_access_token(account)
        headers = {"Authorization": f"Bearer {admin_token}"}
        self.assertEqual(self.client.post("/api/v1/medicine/upload", files=files, headers=headers).status_code, 200)
        with analytics.connect() as db:
            db.execute("UPDATE admin_accounts SET active = 0")
        self.assertEqual(self.client.post("/api/v1/medicine/upload", files=files, headers=headers).status_code, 401)

    def test_missing_bearer_token_is_rejected(self):
        response = self.client.post("/api/v1/skin/local-rag", data={"meeting_id": "meeting", "consent": "true", "processing_consent": "true"},
                                    files={"image": ("snapshot.jpg", self.image, "image/jpeg")})
        self.assertIn(response.status_code, (401, 403))
        self.inference.assert_not_called()

    def upload_personal(self, text=b"Redness can follow skin irritation. Avoid harsh cleansers.", token=None, filename="my-reference.txt"):
        return self.client.post("/api/v1/skin/documents", data={"meeting_id": "meeting"},
            files={"file": (filename, text, "text/plain")},
            headers={"Authorization": f"Bearer {token or self.token()}"})

    def query_personal(self, query, identity="human_test", key="test-agent-event-signing-key-32-characters"):
        return self.client.post("/api/v1/agent/documents/query",
            json={"room_name": "room", "participant_id": identity, "query": query},
            headers={"X-Agent-Event-Key": key})

    def test_participant_upload_and_source_based_spoken_answer(self):
        self.assertEqual(self.upload_personal().status_code, 200)
        response = self.query_personal("What does my document say about redness?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["has_matches"])
        self.assertIn("my-reference.txt", response.json()["answer"])
        self.assertIn("Redness can follow skin irritation.", response.json()["answer"])
        self.store.assert_not_called()  # Never pollute the shared admin corpus.

    def test_personal_documents_are_isolated_and_agent_endpoint_is_protected(self):
        self.upload_personal()
        analytics.add_participant("meeting", "human_other", "Other")
        self.assertFalse(self.query_personal("redness", identity="human_other").json()["has_documents"])
        self.assertEqual(self.query_personal("redness", key="wrong").status_code, 403)
        self.assertEqual(self.upload_personal(token=self.token(room="other")).status_code, 403)

    def test_no_matching_passage_returns_explicit_refusal(self):
        self.upload_personal()
        response = self.query_personal("quantum galaxies")
        self.assertTrue(response.json()["has_documents"])
        self.assertFalse(response.json()["has_matches"])
        self.assertEqual(response.json()["answer"], documents.NO_MATCH)

    def test_generic_document_summary_and_expiry(self):
        self.upload_personal()
        self.assertIn("my-reference.txt", self.query_personal("summarize the document").json()["answer"])
        with analytics.connect() as db:
            db.execute("UPDATE participant_documents SET expires_at = 0")
        self.assertFalse(self.query_personal("redness").json()["has_documents"])

    def test_skin_analysis_retrieves_only_personal_files(self):
        self.upload_personal()
        def infer(_, reference_retriever):
            return {"success": True, "skin_observations": {"face_visible": True, "image_usable": True,
                    "visible_features": ["redness"], "quality_note": "clear"},
                    "medicine_rag": reference_retriever(["redness"])}
        self.inference.side_effect = infer
        response = self.analyze()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("my-reference.txt", response.json()["medical_explanation"])
        self.assertEqual(response.json()["medicine_rag"]["results"][0]["source"], "my-reference.txt")

    def test_upload_validation_and_count_limit(self):
        self.assertEqual(self.upload_personal(filename="unsafe.exe").status_code, 400)
        self.assertEqual(self.upload_personal(text=b"\x00binary").status_code, 422)
        for _ in range(5):
            self.assertEqual(self.upload_personal().status_code, 200)
        self.assertEqual(self.upload_personal().status_code, 429)


if __name__ == "__main__":
    unittest.main()
