"""Chat model factory (role C). Local and free by default: Ollama running in Docker."""

from typing import Literal

from app.config import Settings

ModelSize = Literal["small", "large"]


def get_chat_model(settings: Settings, size: ModelSize = "large"):
    """Return a LangChain chat model.

    `llm_provider="ollama"` gives ChatOllama (`ollama_model_small` for routing/extraction,
    `ollama_model_large` for planning/writing); `"fake"` gives a deterministic scripted model able to
    emit tool calls, used by every test and by CI (never a real model there).
    """
    raise NotImplementedError("C4: get_chat_model")
