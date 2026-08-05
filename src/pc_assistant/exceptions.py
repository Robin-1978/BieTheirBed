from __future__ import annotations


class PCAssistantError(Exception):
    """Base exception for all pc_assistant errors."""


class LLMError(PCAssistantError):
    """Error communicating with LLM provider."""


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


class LLMConnectionError(LLMError):
    """Cannot connect to LLM server."""


class LLMRateLimitError(LLMError):
    """LLM rate limit exceeded."""


class LLMStreamError(LLMError):
    """Error during LLM streaming response."""


class ToolError(PCAssistantError):
    """Error executing a tool."""


class ToolNotFoundError(ToolError, KeyError):
    """Requested tool does not exist in registry."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        ToolError.__init__(self, f"Tool '{tool_name}' not found in registry")


class ToolExecutionError(ToolError):
    """Tool execution failed."""

    def __init__(self, tool_name: str, cause: Exception | str) -> None:
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}' failed: {cause}")


class VerificationError(PCAssistantError):
    """Tool call rejected by the SDB verifier."""

    def __init__(self, tool_name: str, code: str, reason: str) -> None:
        self.tool_name = tool_name
        self.code = code
        self.reason = reason
        super().__init__(f"Verification failed for '{tool_name}': [{code}] {reason}")


class SafetyError(PCAssistantError):
    """Operation blocked by safety checker."""


class SessionError(PCAssistantError):
    """Session-related error."""


class ContextError(PCAssistantError):
    """Context/truncation related error."""


class ConfigError(PCAssistantError):
    """Configuration error."""


class MemoryError(PCAssistantError):
    """Memory storage/retrieval error."""
