import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as api_app
import db


class ConversationApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.original_engine = db.engine
        self.original_session_local = db.SessionLocal
        db.engine = self.engine
        db.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        db.init_db()
        db.upsert_user(
            user_id="google-owner",
            email="owner@example.test",
            name="Owner",
            picture=None,
        )
        api_app.app.dependency_overrides[api_app.authenticated_user] = lambda: {
            "id": "google-owner"
        }
        self.client = TestClient(api_app.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        api_app.app.dependency_overrides.clear()
        db.SessionLocal = self.original_session_local
        db.engine = self.original_engine
        self.engine.dispose()

    def test_owner_scoped_history_preserves_legacy_unowned_conversations(self):
        legacy = db.SessionLocal()
        legacy.add(db.Conversation(thread_id="legacy-thread", title="Legacy", user_id=None))
        legacy.commit()
        legacy.close()

        created = self.client.post(
            "/api/conversations",
            json={"title": "Research", "modelId": "gpt-4o-mini"},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["id"]

        db.save_chat_message(
            conversation_id,
            "google-owner",
            "user",
            "First message",
        )
        db.save_chat_message(
            conversation_id,
            "google-owner",
            "assistant",
            "First response",
        )

        listing = self.client.get("/api/conversations")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item["id"] for item in listing.json()], [conversation_id])
        conversation = self.client.get(f"/api/conversations/{conversation_id}").json()
        self.assertEqual(
            [message["content"] for message in conversation["messages"]],
            ["First message", "First response"],
        )
        self.assertEqual(self.client.get("/api/conversations/legacy-thread").status_code, 404)
        self.assertIsNone(
            db.SessionLocal().query(db.Conversation)
            .filter_by(thread_id="legacy-thread")
            .one()
            .user_id
        )

        api_app.app.dependency_overrides[api_app.authenticated_user] = lambda: {
            "id": "different-google-user"
        }
        self.assertEqual(self.client.get(f"/api/conversations/{conversation_id}").status_code, 404)
        self.assertEqual(
            self.client.delete(
                f"/api/conversations/{conversation_id}",
                headers={"Origin": "http://localhost:5173"},
            ).status_code,
            404,
        )

    def test_model_catalog_validates_selection_and_delete_is_owner_scoped(self):
        models = self.client.get("/api/models")
        self.assertEqual(models.status_code, 200)
        self.assertEqual([model["id"] for model in models.json()], ["gpt-4o-mini"])
        invalid = self.client.post(
            "/api/conversations",
            json={"modelId": "gpt-4o"},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(invalid.status_code, 422)

        created = self.client.post(
            "/api/conversations",
            json={"modelId": "gpt-4o-mini"},
            headers={"Origin": "http://localhost:5173"},
        )
        conversation_id = created.json()["id"]
        deleted = self.client.delete(
            f"/api/conversations/{conversation_id}",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/conversations/{conversation_id}").status_code, 404)

    def test_event_replay_is_ordered_and_owner_authorized(self):
        created = db.create_conversation(
            thread_id="event-thread",
            user_id="google-owner",
            model_id="gpt-4o-mini",
        )
        run, _ = db.create_run_with_user_message(
            run_id="event-run",
            thread_id=created.thread_id,
            user_id="google-owner",
            model_id="gpt-4o-mini",
            content="hello",
            attachment_ids=[],
        )
        db.append_run_event(run_id=run.id, event_type="status", payload={"status": "queued"})
        db.append_run_event(run_id=run.id, event_type="assistant_delta", payload={"delta": "hi"})
        db.append_run_event(run_id=run.id, event_type="status", payload={"status": "completed"})
        db.append_run_event(run_id=run.id, event_type="completed", payload={})

        replay = self.client.get("/api/runs/event-run/events?afterSequence=1")
        self.assertEqual(replay.status_code, 200)
        self.assertIn("id: 2", replay.text)
        self.assertIn("id: 3", replay.text)
        self.assertIn("id: 2\nevent: status", replay.text)

        api_app.app.dependency_overrides[api_app.authenticated_user] = lambda: {
            "id": "different-google-user"
        }
        self.assertEqual(self.client.get("/api/runs/event-run/events").status_code, 404)

    def test_upload_persists_private_attachment_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(api_app, "UPLOAD_DIR", Path(directory)):
                conversation = self.client.post(
                    "/api/conversations",
                    json={"modelId": "gpt-4o-mini"},
                ).json()
                rag_module = ModuleType("rag")
                rag_module.add_document_to_rag = lambda *_args: {"chunks": 1}
                rag_module.delete_document_from_rag = lambda *_args: None
                with patch.dict("sys.modules", {"rag": rag_module}):
                    response = self.client.post(
                        f"/api/conversations/{conversation['id']}/attachments",
                        files={"file": ("notes.txt", b"private note", "text/plain")},
                        headers={"Origin": "http://localhost:5173"},
                    )
                self.assertEqual(response.status_code, 201, response.text)
                attachment = response.json()
                self.assertEqual(attachment["filename"], "notes.txt")
                self.assertEqual(attachment["sizeBytes"], len(b"private note"))
                self.assertEqual(attachment["indexingStatus"], "indexed")
                self.assertEqual((Path(directory) / f"{attachment['id']}.txt").read_bytes(), b"private note")
                stored_attachment = db.get_owned_attachments([attachment["id"]], "google-owner")[0]
                self.assertEqual(stored_attachment.thread_id, conversation["id"])
                self.assertEqual(stored_attachment.indexing_status, "indexed")

    def test_upload_and_run_reject_documents_from_another_conversation(self):
        first = self.client.post("/api/conversations", json={"modelId": "gpt-4o-mini"}).json()
        second = self.client.post("/api/conversations", json={"modelId": "gpt-4o-mini"}).json()
        rag_module = ModuleType("rag")
        rag_module.add_document_to_rag = lambda *_args: {"chunks": 1}
        rag_module.delete_document_from_rag = lambda *_args: None
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(api_app, "UPLOAD_DIR", Path(directory)), patch.dict(
                "sys.modules", {"rag": rag_module}
            ):
                uploaded = self.client.post(
                    f"/api/conversations/{first['id']}/attachments",
                    files={"file": ("notes.txt", b"private note", "text/plain")},
                )
                self.assertEqual(uploaded.status_code, 201, uploaded.text)
                foreign_upload = self.client.post(
                    "/api/conversations/not-owned/attachments",
                    files={"file": ("notes.txt", b"private note", "text/plain")},
                )
                self.assertEqual(foreign_upload.status_code, 404)

                run = self.client.post(
                    f"/api/conversations/{second['id']}/runs",
                    json={
                        "modelId": "gpt-4o-mini",
                        "content": "read this",
                        "attachments": [uploaded.json()["id"]],
                    },
                )
                self.assertEqual(run.status_code, 422, run.text)

    def test_upload_rejects_invalid_content_and_oversized_files(self):
        conversation = self.client.post("/api/conversations", json={"modelId": "gpt-4o-mini"}).json()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(api_app, "UPLOAD_DIR", Path(directory)):
                invalid_content = self.client.post(
                    f"/api/conversations/{conversation['id']}/attachments",
                    files={"file": ("notes.pdf", b"not a pdf", "application/pdf")},
                )
                oversized = self.client.post(
                    f"/api/conversations/{conversation['id']}/attachments",
                    files={
                        "file": (
                            "notes.txt",
                            b"x" * (api_app.MAX_UPLOAD_BYTES + 1),
                            "text/plain",
                        )
                    },
                )
            self.assertEqual(invalid_content.status_code, 415)
            self.assertEqual(oversized.status_code, 413)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_indexing_failure_removes_upload_and_reports_error(self):
        conversation = self.client.post("/api/conversations", json={"modelId": "gpt-4o-mini"}).json()
        rag_module = ModuleType("rag")
        rag_module.add_document_to_rag = lambda *_args: (_ for _ in ()).throw(RuntimeError("embedding unavailable"))
        rag_module.delete_document_from_rag = lambda *_args: None
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(api_app, "UPLOAD_DIR", Path(directory)), patch.dict(
                "sys.modules", {"rag": rag_module}
            ):
                response = self.client.post(
                    f"/api/conversations/{conversation['id']}/attachments",
                    files={"file": ("notes.txt", b"private note", "text/plain")},
                )
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn("could not be indexed", response.json()["detail"])
            self.assertEqual(list(Path(directory).iterdir()), [])
            session = db.SessionLocal()
            try:
                self.assertEqual(session.query(db.Attachment).count(), 0)
            finally:
                session.close()

    async def test_delete_stops_active_run_and_cleans_conversation_data(self):
        conversation = db.create_conversation(
            thread_id="delete-active-thread",
            user_id="google-owner",
            model_id="gpt-4o-mini",
        )
        run, _ = db.create_run_with_user_message(
            run_id="delete-active-run",
            thread_id=conversation.thread_id,
            user_id="google-owner",
            model_id="gpt-4o-mini",
            content="hello",
            attachment_ids=[],
        )
        db.append_run_event(run_id=run.id, event_type="status", payload={"status": "queued"})
        with tempfile.TemporaryDirectory() as directory:
            attachment_path = Path(directory) / "delete-me.txt"
            attachment_path.write_text("indexed content", encoding="utf-8")
            db.create_attachment(
                attachment_id="delete-me-attachment",
                user_id="google-owner",
                thread_id=conversation.thread_id,
                filename="delete-me.txt",
                content_type="text/plain",
                size_bytes=15,
                storage_key=attachment_path.name,
                indexing_status="indexed",
            )
            rejected = self.client.delete(f"/api/conversations/{conversation.thread_id}")
            self.assertEqual(rejected.status_code, 409)
            rag_module = ModuleType("rag")
            cleaned_threads = []
            rag_module.delete_conversation_documents = lambda thread: cleaned_threads.append(thread)
            with patch.object(api_app, "UPLOAD_DIR", Path(directory)), patch.dict(
                "sys.modules", {"rag": rag_module}
            ):
                deleted = self.client.delete(
                    f"/api/conversations/{conversation.thread_id}?stopActiveRun=true",
                )
            self.assertFalse(attachment_path.exists())
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(cleaned_threads, [conversation.thread_id])
        session = db.SessionLocal()
        try:
            self.assertIsNone(
                session.query(db.Conversation)
                .filter_by(thread_id=conversation.thread_id)
                .first()
            )
            self.assertIsNone(session.query(db.ChatRun).filter_by(id=run.id).first())
            self.assertEqual(
                session.query(db.ChatMessage)
                .filter_by(thread_id=conversation.thread_id)
                .count(),
                0,
            )
            self.assertIsNone(
                session.query(db.Attachment)
                .filter_by(id="delete-me-attachment")
                .first()
            )
        finally:
            session.close()
        self.assertEqual(db.get_run_events(run.id), [])

    async def test_agent_execution_persists_deltas_and_final_message_before_completion(self):
        conversation = db.create_conversation(
            thread_id="stream-thread",
            user_id="google-owner",
            model_id="gpt-4o-mini",
        )
        run, _ = db.create_run_with_user_message(
            run_id="stream-run",
            thread_id=conversation.thread_id,
            user_id="google-owner",
            model_id="gpt-4o-mini",
            content="hello",
            attachment_ids=[],
        )
        db.append_run_event(run_id=run.id, event_type="status", payload={"status": "queued"})

        class FakeAgent:
            async def astream_events(self, *_args, **_kwargs):
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content="Hello")},
                }
                yield {"event": "on_tool_start", "name": "calculator", "data": {}}
                yield {"event": "on_tool_end", "name": "calculator", "data": {}}
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content=" there.")},
                }

            async def aget_state(self, _config):
                return SimpleNamespace(tasks=[])

        agent_module = ModuleType("agents")
        agent_module.get_agent = lambda _model_id: FakeAgent()
        with patch.dict("sys.modules", {"agents": agent_module}):
            await api_app.execute_run(
                "stream-run",
                conversation.thread_id,
                "google-owner",
                "gpt-4o-mini",
                "hello",
                [],
            )

        events = db.get_run_events("stream-run")
        self.assertEqual([event["sequence"] for event in events], list(range(len(events))))
        self.assertEqual(
            [event["type"] for event in events],
            [
                "status",
                "status",
                "assistant_delta",
                "tool_call",
                "tool_result",
                "assistant_delta",
                "status",
                "completed",
            ],
        )
        self.assertEqual(
            [message.content for message in db.get_chat_history(conversation.thread_id, "google-owner")],
            ["hello", "Hello there."],
        )
        self.assertEqual(events[3]["payload"], {"name": "calculator"})

    async def test_agent_interrupt_is_not_reported_as_completed(self):
        conversation = db.create_conversation(
            thread_id="paused-thread",
            user_id="google-owner",
            model_id="gpt-4o-mini",
        )
        run, _ = db.create_run_with_user_message(
            run_id="paused-run",
            thread_id=conversation.thread_id,
            user_id="google-owner",
            model_id="gpt-4o-mini",
            content="approve this action",
            attachment_ids=[],
        )

        class PausedAgent:
            async def astream_events(self, *_args, **_kwargs):
                if False:
                    yield {}

            async def aget_state(self, _config):
                return SimpleNamespace(tasks=[SimpleNamespace(interrupts=["pending"])])

        agent_module = ModuleType("agents")
        agent_module.get_agent = lambda _model_id: PausedAgent()
        with patch.dict("sys.modules", {"agents": agent_module}):
            await api_app.execute_run(
                run.id,
                conversation.thread_id,
                "google-owner",
                "gpt-4o-mini",
                "approve this action",
                [],
            )

        events = db.get_run_events(run.id)
        self.assertNotIn("completed", [event["type"] for event in events])
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-2]["payload"], {"status": "failed"})


if __name__ == "__main__":
    unittest.main()
