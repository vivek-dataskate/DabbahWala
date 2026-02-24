"""
LLM Service — single point of contact for all Anthropic Claude API calls.

All Python code that needs Claude should import from here.
This keeps API key management, model selection, and error handling in one place.

# Important: tool_use IDs must be unique across all messages in a conversation.
#
# When building multi-turn agentic conversations, ALWAYS:
#   1. Append the full assistant response object to messages as-is (do NOT reconstruct it).
#   2. Reference block.id directly from the response when creating tool_result messages.
#   3. Never include the same assistant turn twice in a messages list.
#
# Violating these rules causes:
#   API Error 400: "messages.N.content.M: tool_use ids must be unique"
#
# Use call_claude_agentic() for multi-turn tool-use conversations.
"""

import logging
import os
from typing import Callable

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


def call_claude_agentic(
    system: str,
    user: str,
    tools: list[dict],
    tool_handler: Callable[[str, dict], str],
    model: str = MODEL,
    max_tokens: int = 4096,
    max_rounds: int = 10,
) -> str:
    """Run a multi-turn agentic conversation with Claude that uses tools.

    This function correctly manages the conversation history to guarantee that
    tool_use IDs are unique across all messages. Duplicate IDs cause:
        API Error 400: "messages.N.content.M: tool_use ids must be unique"

    The correct pattern:
      1. Never reconstruct or copy assistant content blocks — always append
         the response's content list directly as the assistant turn.
      2. Build tool_result blocks using block.id from the response (not
         self-generated IDs which could collide on retry).
      3. Stop as soon as stop_reason is "end_turn" or there are no tool_use
         blocks in the response.

    Args:
        system:       System prompt.
        user:         Initial user message.
        tools:        List of tool definition dicts.
        tool_handler: Callable(tool_name, tool_input) -> result_string.
                      Called for each tool_use block in Claude's response.
        model:        Model ID.
        max_tokens:   Max tokens per response.
        max_rounds:   Safety cap on agentic rounds to prevent infinite loops.

    Returns:
        The final text response from Claude after all tool calls are resolved.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not configured.
        anthropic.APIError: On API-level errors.
        RuntimeError: If max_rounds is exceeded without a final answer.
    """
    client = get_client()

    # Start with a single user message. Never pre-populate with assistant turns
    # — doing so is the primary source of duplicate tool_use IDs.
    messages: list[dict] = [{"role": "user", "content": user}]

    for round_num in range(max_rounds):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        logger.debug(
            "Agentic round %d: stop_reason=%s blocks=%d",
            round_num + 1,
            response.stop_reason,
            len(response.content),
        )

        # Collect tool_use blocks from this response.
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason == "end_turn" or not tool_use_blocks:
            # Extract and return the final text answer.
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        # Append the assistant's full response to history.
        # CRITICAL: use response.content directly — do NOT reconstruct content
        # blocks. Reconstructing risks duplicating IDs if the same response is
        # processed more than once (e.g. on a retry after a transient failure).
        messages.append({"role": "assistant", "content": response.content})

        # Execute each tool and collect results.
        # IDs come from block.id (server-assigned) — never generate our own.
        tool_results = []
        for block in tool_use_blocks:
            try:
                result = tool_handler(block.name, block.input or {})
            except Exception as exc:
                logger.warning("Tool %s raised: %s", block.name, exc)
                result = f"Error: {exc}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,   # server-assigned ID — always unique per turn
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(
        f"Agentic loop exceeded max_rounds={max_rounds} without reaching end_turn. "
        "Increase max_rounds or check that tool_handler terminates the loop."
    )
