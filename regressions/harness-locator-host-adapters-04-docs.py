"""Load the published harness.example.toml through the real catalog loader."""

from __future__ import annotations

from pathlib import Path

from molmcp.components import load_harness_catalog
from molmcp.components.models import ComponentKind

_SHA = "9f1c3b2a7d4e0165c8a9b3d27e5f10486c73ab92"
_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "concepts"
    / "harness.example.toml"
)


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "harness.toml").write_text(
            _EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        catalog = load_harness_catalog(
            root, _SHA, frozenset({"provider-sdk", "harness-catalog"})
        )
        if catalog.sha != _SHA:
            raise SystemExit(f"sha {catalog.sha!r}")
        names = {bundle.name for bundle in catalog.bundles}
        if names != {"sci", "dev"}:
            raise SystemExit(f"bundles {names!r}")
        kinds = {spec.kind for spec in catalog.components}
        if kinds != set(ComponentKind):
            raise SystemExit(f"kinds {kinds!r}")
    print("harness-locator-host-adapters-04-docs: ok")


if __name__ == "__main__":
    main()
