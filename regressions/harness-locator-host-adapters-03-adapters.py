"""Hard-coded goldens for per-host frontmatter remap.

Public API: remap_frontmatter via install_skill / place_components.
"""

from __future__ import annotations

from molmcp.host import ADAPTER_TEXT
from molmcp.host.layout import remap_frontmatter

_INPUT = """\
---
name: daily
description: A daily skill
when-to-use: every morning
user-invocable: false
disable-model-invocation: true
argument-hint: "<topic>"
tools: Read, Grep
---
# body
"""


def main() -> None:
    grok = remap_frontmatter(_INPUT, "grok")
    if "when-to-use: every morning" not in grok:
        raise SystemExit("grok lost when-to-use")
    if "tools:" in grok:
        raise SystemExit("grok kept tools")
    claude = remap_frontmatter(_INPUT, "claude")
    if "when-to-use:" in claude:
        raise SystemExit("claude kept when-to-use")
    if "molmcp-dev" in ADAPTER_TEXT or "commands/" in ADAPTER_TEXT:
        raise SystemExit("ADAPTER_TEXT still names molmcp-dev or commands/")
    print("harness-locator-host-adapters-03-adapters: ok")


if __name__ == "__main__":
    main()
