import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

import agents


class FakeChatModel:
    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, _messages):
        return AIMessage(content="Hello")


class AgentCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_runs_with_async_sqlite_checkpointer(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "checkpoints.sqlite"
            with (
                patch.object(agents, "CHECKPOINT_DB_PATH", checkpoint_path),
                patch.object(agents, "ChatOpenAI", return_value=FakeChatModel()),
            ):
                async with agents.agent_runtime():
                    graph = agents.get_agent("gpt-4o-mini")
                    config = {"configurable": {"thread_id": "async-checkpoint-test"}}

                    result = await graph.ainvoke(
                        {"messages": [HumanMessage(content="Hi")]},
                        config=config,
                    )
                    snapshot = await graph.aget_state(config)

            self.assertEqual(result["messages"][-1].content, "Hello")
            self.assertEqual(snapshot.values["messages"][-1].content, "Hello")
            self.assertTrue(checkpoint_path.exists())


if __name__ == "__main__":
    unittest.main()
