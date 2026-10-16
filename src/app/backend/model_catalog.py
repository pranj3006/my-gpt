"""Server-owned model IDs offered by the chat API."""

MODEL_OPTIONS = (
    {
        "id": "gpt-4o-mini",
        "name": "GPT-4o mini",
        "description": "Faster for everyday tasks",
    },
)

MODEL_IDS = frozenset(option["id"] for option in MODEL_OPTIONS)
DEFAULT_MODEL_ID = "gpt-4o-mini"
