"""
LLM Service — single point of contact for all Anthropic Claude API calls.

All Python code that needs Claude should import from here.
This keeps API key management, model selection, and error handling in one place.
"""

import logging
import os

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"


def get_client() -> anthropic.Anthropic:
    """Return an authenticated Anthropic client. Raises ValueError if key is missing."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def call_claude(system: str, user: str, model: str = MODEL, max_tokens: int = 2000) -> str:
    """Call Claude and return the text response.

    Args:
        system: System prompt string.
        user:   User message string.
        model:  Model ID (defaults to MODULE-level MODEL constant).
        max_tokens: Maximum tokens in the response.

    Returns:
        The assistant's text response.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not configured.
        anthropic.APIError: On API-level errors.
    """
    client = get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text if response.content else ""


def call_claude_tool(system: str, user: str, tool: dict, model: str = MODEL) -> dict:
    """Call Claude with a single forced tool and return the tool input dict.

    Args:
        system: System prompt string.
        user:   User message string.
        tool:   Tool definition dict (name, description, input_schema).
        model:  Model ID.

    Returns:
        The tool's input dict as returned by Claude.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not configured or no tool use found.
    """
    client = get_client()
    tool_name = tool.get("name", "unknown")
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input or {}
    logger.warning("Claude returned no tool_use block for tool=%s", tool_name)
    return {}
