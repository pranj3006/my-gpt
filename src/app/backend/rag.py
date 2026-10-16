"""Conversation-scoped document ingestion and retrieval for the chatbot agent."""

import hashlib
from functools import lru_cache
from pathlib import Path

import docx2txt
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


BACKEND_DIR = Path(__file__).resolve().parent
CHROMA_DIR = BACKEND_DIR / "data" / "rag" / "chroma_db"
COLLECTION_NAME = "my_gpt_documents"

load_dotenv(BACKEND_DIR / ".env")


@lru_cache(maxsize=1)
def _get_vectorstore() -> Chroma:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
    )


@lru_cache(maxsize=1)
def _get_vectorstore_for_cleanup() -> Chroma:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=None,
        persist_directory=str(CHROMA_DIR),
    )


def _validated_thread_id(thread_id: str) -> str:
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("A conversation ID is required for document operations.")
    return thread_id.strip()


def _source_key(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def read_file_text(file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Document does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if suffix == ".docx":
        return docx2txt.process(str(path))

    if suffix in {".txt", ".md", ".py", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")

    raise ValueError("Unsupported file type. Use PDF, DOCX, TXT, MD, PY, or CSV.")


def add_document_to_rag(file_path: str | Path, thread_id: str) -> dict[str, str | int]:
    thread_id = _validated_thread_id(thread_id)
    path = Path(file_path)
    text = read_file_text(path)
    if not text.strip():
        raise ValueError("No text could be extracted from this file.")

    chunks = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=150,
    ).split_text(text)

    source_key = _source_key(path)
    documents = [
        Document(
            page_content=chunk,
            metadata={
                "thread_id": thread_id,
                "source": path.name,
                "source_key": source_key,
            },
        )
        for chunk in chunks
    ]
    document_ids = [
        hashlib.sha256(
            f"{thread_id}\0{source_key}\0{index}".encode("utf-8")
        ).hexdigest()
        for index in range(len(documents))
    ]

    vectorstore = _get_vectorstore()
    existing_ids = vectorstore.get(
        where={"thread_id": thread_id, "source_key": source_key},
        include=["metadatas"],
    )["ids"]
    vectorstore.add_documents(documents, ids=document_ids)

    stale_ids = set(existing_ids).difference(document_ids)
    if stale_ids:
        vectorstore.delete(ids=sorted(stale_ids))

    return {"filename": path.name, "chunks": len(documents)}


def delete_document_from_rag(file_path: str | Path, thread_id: str) -> None:
    thread_id = _validated_thread_id(thread_id)
    source_key = _source_key(Path(file_path))
    vectorstore = _get_vectorstore_for_cleanup()
    existing_ids = vectorstore.get(
        where={"thread_id": thread_id, "source_key": source_key},
        include=["metadatas"],
    )["ids"]
    if existing_ids:
        vectorstore.delete(ids=existing_ids)


def delete_conversation_documents(thread_id: str) -> None:
    thread_id = _validated_thread_id(thread_id)
    vectorstore = _get_vectorstore_for_cleanup()
    document_ids = vectorstore.get(
        where={"thread_id": thread_id},
        include=["metadatas"],
    )["ids"]
    if document_ids:
        vectorstore.delete(ids=document_ids)


def retrieve_from_rag(query: str, thread_id: str, k: int = 4) -> str:
    thread_id = _validated_thread_id(thread_id)
    if not query.strip():
        raise ValueError("A search query is required.")
    if k < 1:
        raise ValueError("The number of results must be positive.")

    documents = _get_vectorstore().similarity_search(
        query,
        k=k,
        filter={"thread_id": thread_id},
    )
    if not documents:
        return "No relevant uploaded document content found."

    return "\n\n".join(
        f"[Source {index}: {document.metadata.get('source', 'uploaded document')}]\n"
        f"{document.page_content}"
        for index, document in enumerate(documents, start=1)
    )


@tool
def rag_tool(query: str, config: RunnableConfig) -> str:
    """Search documents uploaded to the active conversation."""
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("Document search requires an active conversation.")
    return retrieve_from_rag(query, thread_id)
