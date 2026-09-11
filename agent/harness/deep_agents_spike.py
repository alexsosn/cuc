"""Temporary executable probe for HARN-007.

This module intentionally depends on the pinned Deep Agents spike dependency.  It is
not production parsing infrastructure and should be removed with that dependency after
the ADR is recorded unless Deep Agents is adopted as a runtime dependency.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from importlib.metadata import version
from typing import Any

from deepagents.graph import create_deep_agent
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from pydantic import Field


_REQUIRED_TOKEN_IDS = ("t1", "t2", "t3")


class _FixedGenericFakeChatModel(GenericFakeChatModel):
    """Upstream-compatible fake chat model whose scripted iterator survives tracing."""

    messages: Iterator[AIMessage | str] = Field(exclude=True)
    llm_type: str = "generic-fake-chat-model"

    @property
    def _llm_type(self) -> str:
        return self.llm_type

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def run_early_termination_probe() -> dict[str, object]:
    """Observe whether the default Deep Agents loop enforces complete traversal.

    The scripted model is explicitly asked to review a three-token complete column. It
    calls the provided review tool for only t1, then emits an ordinary final response.
    If `create_deep_agent` returns normally, the framework's primary control loop has
    not independently enforced CUC's every-token completion invariant.
    """

    observed: list[str] = []

    @tool
    def review_token(token_id: str) -> str:
        """Review one token from the fixed complete-column workload."""

        observed.append(token_id)
        return f"reviewed:{token_id}"

    model = _FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "review_token",
                            "args": {"token_id": "t1"},
                            "id": "call-review-t1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Done after one token."),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=[review_token],
        system_prompt=(
            "Review the complete fixed column. Every token t1, t2, t3 must be reviewed "
            "in textual order before completion."
        ),
    )
    result = agent.invoke(
        {
            "messages": [
                HumanMessage(content="Review the complete column t1, t2, t3 in order.")
            ]
        }
    )
    final_message = result["messages"][-1]
    final_content = final_message.content
    if not isinstance(final_content, str):
        raise ValueError("probe final response must be plain text")

    tool_results = tuple(message for message in result["messages"] if message.type == "tool")
    return {
        "deepagents_version": version("deepagents"),
        "required_token_ids": _REQUIRED_TOKEN_IDS,
        "observed_token_ids": tuple(observed),
        "completed_normally": True,
        "final_response": final_content,
        "tool_result_count": len(tool_results),
    }
