"""Chat model factory (role C). Local and free by default: Ollama running in Docker."""

from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from app.config import Settings

ModelSize = Literal["small", "large"]


def get_chat_model(settings: Settings, size: ModelSize = "large"):
    """Return a LangChain chat model.

    `llm_provider="ollama"` gives ChatOllama (`ollama_model_small` for routing/extraction,
    `ollama_model_large` for planning/writing); `"fake"` gives a deterministic scripted model able to
    emit tool calls, used by every test and by CI (never a real model there).
    """
    if settings.llm_provider == "fake":
        return ScriptedChatModel()
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama  # pylint: disable=import-outside-toplevel

        name = settings.ollama_model_large if size == "large" else settings.ollama_model_small
        # temperature 0: the same question gives the same plan, which the trace and the tests rely on
        return ChatOllama(model=name, base_url=settings.ollama_base_url, temperature=0)
    raise ValueError(f"Unknown LLM_PROVIDER {settings.llm_provider!r}: use 'ollama' or 'fake'")


# Streaming (_stream) is optional in LangChain and the agent never streams.
class ScriptedChatModel(BaseChatModel):  # pylint: disable=abstract-method
    """A chat model that plays back a script, for tests and CI.

    Each call takes the next item of `script`: an `AIMessage` (possibly with tool calls) for a normal call,
    a `dict` for `with_structured_output`. When the script is empty, a normal call answers an empty message
    (no tool call, so the agent stops planning) and a structured call raises, like a model that returns
    unusable JSON. `prompts` records what the model was sent.
    """

    script: list[Any] = Field(default_factory=list)
    prompts: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        """Name used by LangChain callbacks."""
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Return the next scripted message."""
        del stop, run_manager, kwargs
        self.prompts.append(list(messages))
        item = self.script.pop(0) if self.script else AIMessage(content="")
        if not isinstance(item, AIMessage):
            raise ValueError(f"the script expected a structured call here, got a chat call ({item!r})")
        return ChatResult(generations=[ChatGeneration(message=item)])

    def bind_tools(self, tools, **kwargs):  # pylint: disable=arguments-differ
        """Tools change nothing: the script already says which tool to call."""
        del tools, kwargs
        return self

    def with_structured_output(self, schema, **kwargs):  # pylint: disable=arguments-differ
        """A runnable that validates the next scripted dict against `schema`."""
        del kwargs

        def respond(prompt):
            self.prompts.append(prompt)
            if not self.script or not isinstance(self.script[0], dict):
                raise ValueError("no structured answer left in the script")
            return schema.model_validate(self.script.pop(0))

        return RunnableLambda(respond)


def tool_call(name: str, args: dict | None = None, call_id: str | None = None) -> AIMessage:
    """A scripted assistant message asking for one tool call (helper for tests and demos)."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id or f"call_{name}"}])
