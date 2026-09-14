"""Minimal in-process provider used to drive the worker subprocess."""

from __future__ import annotations

from molmcp.provider_sdk import READ_ONLY, ProviderBase, tool


class EchoProvider(ProviderBase):
    """Echo plane — one read-only tool."""

    name = "echo"

    @tool(READ_ONLY)
    def echo(self, text: str) -> dict[str, str]:
        """Echo text back."""
        return {"text": text}
