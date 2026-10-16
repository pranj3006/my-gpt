import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["LANGSMITH_TRACING"] = "false"

import rag


class InMemoryVectorStore:
    def __init__(self):
        self.documents = {}
        self.last_filter = None

    def get(self, where, include):
        ids = [
            document_id
            for document_id, document in self.documents.items()
            if all(document.metadata.get(key) == value for key, value in where.items())
        ]
        return {"ids": ids}

    def add_documents(self, documents, ids):
        self.documents.update(zip(ids, documents))

    def delete(self, ids):
        for document_id in ids:
            self.documents.pop(document_id, None)

    def similarity_search(self, query, k, filter):
        self.last_filter = filter
        matches = [
            document
            for document in self.documents.values()
            if document.metadata.get("thread_id") == filter["thread_id"]
        ]
        return matches[:k]


class RagTests(unittest.TestCase):
    def test_ingestion_replaces_stale_chunks_and_keeps_conversations_separate(self):
        vectorstore = InMemoryVectorStore()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("original " * 350, encoding="utf-8")

            with patch.object(rag, "_get_vectorstore", return_value=vectorstore):
                result = rag.add_document_to_rag(path, "conversation-1")
                original_count = result["chunks"]
                self.assertGreater(original_count, 1)

                path.write_text("updated notes", encoding="utf-8")
                updated = rag.add_document_to_rag(path, "conversation-1")
                self.assertEqual(updated["chunks"], 1)

                rag.add_document_to_rag(path, "conversation-2")
                self.assertEqual(
                    rag.retrieve_from_rag("notes", "conversation-1"),
                    "[Source 1: notes.txt]\nupdated notes",
                )
                self.assertEqual(
                    vectorstore.last_filter,
                    {"thread_id": "conversation-1"},
                )

        thread_ids = {
            document.metadata["thread_id"]
            for document in vectorstore.documents.values()
        }
        self.assertEqual(thread_ids, {"conversation-1", "conversation-2"})
        self.assertEqual(len(vectorstore.documents), 2)

    def test_reads_supported_text_and_rejects_unsupported_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.md"
            path.write_text("hello", encoding="utf-8")
            self.assertEqual(rag.read_file_text(path), "hello")

            unsupported = Path(directory) / "notes.exe"
            unsupported.write_text("data", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported file type"):
                rag.read_file_text(unsupported)

    def test_tool_uses_conversation_from_langgraph_config(self):
        with patch.object(rag, "retrieve_from_rag", return_value="found") as retrieve:
            result = rag.rag_tool.invoke(
                {"query": "find this"},
                config={"configurable": {"thread_id": "conversation-7"}},
            )

        self.assertEqual(result, "found")
        retrieve.assert_called_once_with("find this", "conversation-7")

    def test_tool_rejects_missing_conversation_context(self):
        with self.assertRaisesRegex(ValueError, "active conversation"):
            rag.rag_tool.invoke(
                {"query": "find this"},
                config={"configurable": {}},
            )

    def test_document_and_conversation_cleanup_delete_only_matching_chunks(self):
        vectorstore = InMemoryVectorStore()
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.txt"
            second = Path(directory) / "second.txt"
            first.write_text("first conversation document", encoding="utf-8")
            second.write_text("second conversation document", encoding="utf-8")
            with patch.object(rag, "_get_vectorstore", return_value=vectorstore), patch.object(
                rag,
                "_get_vectorstore_for_cleanup",
                return_value=vectorstore,
            ):
                rag.add_document_to_rag(first, "conversation-1")
                rag.add_document_to_rag(second, "conversation-1")
                rag.add_document_to_rag(first, "conversation-2")

                rag.delete_document_from_rag(first, "conversation-1")
                self.assertEqual(len(vectorstore.documents), 2)
                rag.delete_conversation_documents("conversation-1")

        self.assertEqual(
            {document.metadata["thread_id"] for document in vectorstore.documents.values()},
            {"conversation-2"},
        )


if __name__ == "__main__":
    unittest.main()
