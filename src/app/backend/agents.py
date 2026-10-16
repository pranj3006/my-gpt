import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
import certifi

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from langgraph.graph import StateGraph, START, MessagesState
from typing import TypedDict, Literal,Annotated
from pydantic import BaseModel,Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage,HumanMessage,BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import interrupt,Command
from langgraph.graph.message import add_messages
from tools import tools
from model_catalog import DEFAULT_MODEL_ID, MODEL_IDS


DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CHECKPOINT_DB_PATH = DATA_DIR / "langgraph_checkpoints.sqlite"
_CHECKPOINTER: AsyncSqliteSaver | None = None
_AGENT_CACHE = {}

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", DEFAULT_MODEL_ID)
if DEFAULT_MODEL not in MODEL_IDS:
    raise RuntimeError(f"DEFAULT_MODEL must be one of: {', '.join(sorted(MODEL_IDS))}.")

SYSTEM_PROMPT = """
You are a helpful agentic chatbot with access to several tools.

You can:
1. Answer normal questions.
2. Use tools when needed.
3. Search uploaded couments using the RAG tool.
4. Search the web for latest/current information using Tavily Search.
5. Remember important user information using memory tool.
6. Recall memory when usefull.
7. Use calculator for math

Rules:
- If the user asks about latest news, current events, recent updates, today's information, current prices, current people, current versions, new releases, or anything time-sensitive, use Tavily Search.
- If the user asks about an uploaded document, use search_uploaded_documents.
- If the user asks you to remember something, use remember_this.
- If the user asks about previous preferences or saved facts, use recall_memory.
- Use calculator for math questions.
- When using web search, summarize clearly and mention that the answer is based on web search results.
- Be clear, helpful, and concise.        
"""


@asynccontextmanager
async def agent_runtime() -> AsyncIterator[None]:
    """Keep the async checkpoint connection open for the application lifetime."""
    global _CHECKPOINTER

    if _CHECKPOINTER is not None:
        raise RuntimeError("The agent runtime is already active.")

    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
        _CHECKPOINTER = checkpointer
        try:
            yield
        finally:
            _CHECKPOINTER = None
            _AGENT_CACHE.clear()


def normalize_model_name(model_name: str | None) -> str:
    selected = DEFAULT_MODEL if model_name is None else model_name.strip()
    if selected not in MODEL_IDS:
        raise ValueError("The selected model is not available.")
    return selected

def build_agent(model_name: str):
    """
    Build one LangGraph  agent for selected Model
    """

    selected_model = normalize_model_name(model_name)

    llm = ChatOpenAI(
    model_name=selected_model,
    temperature=0,
    streaming=True)

    llm_with_tools = llm.bind_tools(tools)

    async def chatbot_node(state: MessagesState):
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = await llm_with_tools.ainvoke(messages)

        return {
            "messages": [response]
            }
    
    tool_node = ToolNode(tools)

    workflow = StateGraph(MessagesState)

    workflow.add_node("chatbot",chatbot_node)
    workflow.add_node("tools",tool_node)

    workflow.add_edge(START,"chatbot")
    workflow.add_conditional_edges("chatbot",tools_condition)    
    workflow.add_edge("tools","chatbot")


    checkpointer = _CHECKPOINTER
    if checkpointer is None:
        raise RuntimeError("The agent runtime must be active before building an agent.")

    return workflow.compile(checkpointer=checkpointer)    


def get_agent(model_name: str | None = None):
    """
    Return cached LangGraph agent for selected model.
    If not created yet, create it once and reuse it.
    """

    selected_model = normalize_model_name(model_name)

    if selected_model not in _AGENT_CACHE:
        _AGENT_CACHE[selected_model] = build_agent(selected_model)

    return _AGENT_CACHE[selected_model]