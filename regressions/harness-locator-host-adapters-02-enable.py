"""Hard-coded goldens for HarnessCatalog.enabled_components.

Goldens (literals, not derived from the catalog under test):
- sci then lab → skill.notes, rule.style, agent.reviewer
- empty enable → empty tuple
- unknown name → CatalogError containing unknown-bundle
- zero bundles + None → all three components
"""

from __future__ import annotations

from molmcp.components.catalog import HarnessCatalog
from molmcp.components.models import (
    BundleSpec,
    CatalogError,
    ComponentKind,
    ComponentSpec,
)

_SHA = "0123456789abcdef0123456789abcdef01234567"
_NOTES = ComponentSpec(
    kind=ComponentKind.SKILL,
    name="notes",
    id="skill.notes",
    path="skills/notes/SKILL.md",
)
_STYLE = ComponentSpec(
    kind=ComponentKind.RULE, name="style", id="rule.style", path="rules/style.md"
)
_PLANNER = ComponentSpec(
    kind=ComponentKind.AGENT,
    name="reviewer",
    id="agent.reviewer",
    path="agents/reviewer.md",
)


def _catalog_with_bundles() -> HarnessCatalog:
    return HarnessCatalog(
        sha=_SHA,
        requires=(),
        components=(_NOTES, _STYLE, _PLANNER),
        bundles=(
            BundleSpec(name="sci", members=("skill.notes", "rule.style")),
            BundleSpec(name="lab", members=("skill.notes", "agent.reviewer")),
        ),
    )


def main() -> None:
    catalog = _catalog_with_bundles()
    sci_lab = tuple(spec.id for spec in catalog.enabled_components(("sci", "lab")))
    if sci_lab != ("skill.notes", "rule.style", "agent.reviewer"):
        raise SystemExit(f"sci+lab union: {sci_lab!r}")
    if catalog.enabled_components(()) != ():
        raise SystemExit("empty enable was not empty")
    try:
        catalog.enabled_components(("nope",))
    except CatalogError as exc:
        message = str(exc)
        if "unknown-bundle" not in message or "'sci'" not in message:
            raise SystemExit(f"unknown-bundle message: {message!r}") from exc
    else:
        raise SystemExit("unknown name did not raise")

    empty = HarnessCatalog(
        sha=_SHA, requires=(), components=(_NOTES, _STYLE, _PLANNER), bundles=()
    )
    if empty.enabled_components(None) != (_NOTES, _STYLE, _PLANNER):
        raise SystemExit("zero-bundle None did not return all components")
    if empty.enabled_components(()) != ():
        raise SystemExit("zero-bundle empty enable was not empty")
    print("harness-locator-host-adapters-02-enable: ok")


if __name__ == "__main__":
    main()
