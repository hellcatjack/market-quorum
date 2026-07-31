from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FunctionCall(BaseModel):
    name: str = Field(min_length=1)
    arguments: str


class AssistantToolCall(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_calls: list[AssistantToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> ChatMessage:
        if self.role in {"system", "user"} and self.content is None:
            raise ValueError(f"{self.role} messages require text content")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("tool_calls are only valid on assistant messages")
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("assistant messages require content or tool_calls")
        return self


class FunctionDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class FunctionTool(BaseModel):
    type: Literal["function"] = "function"
    function: FunctionDefinition


class NamedFunctionChoice(BaseModel):
    name: str = Field(min_length=1)


class NamedToolChoice(BaseModel):
    type: Literal["function"] = "function"
    function: NamedFunctionChoice


ToolChoice = Literal["auto", "none", "required"] | NamedToolChoice


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    reasoning_effort: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    tools: list[FunctionTool] = Field(default_factory=list)
    tool_choice: ToolChoice | None = None
    stream: Literal[False] = False
    n: Literal[1] = 1
    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    parallel_tool_calls: bool | None = None

    @field_validator("stream", mode="before")
    @classmethod
    def validate_stream_type(cls, value: Any) -> bool:
        if type(value) is not bool or value is not False:
            raise ValueError("stream must be false")
        return value

    @field_validator("n", mode="before")
    @classmethod
    def validate_n_type(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("n must be the integer 1")
        return value


class GatewayStatus(BaseModel):
    status: Literal["ok"] = "ok"
    accepting: bool = True
    active_completions: int = Field(ge=0)
    oldest_active_seconds: float | None = Field(default=None, ge=0)
    stalest_progress_seconds: float | None = Field(default=None, ge=0)
    model: str
    reasoning_effort: str
    snapshot_id: str


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class CodexTurnResult:
    final_message: str
    usage: TokenUsage
