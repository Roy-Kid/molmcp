# Harness contract — two long-lived rules

Two rules only. Everything else about the harness — the catalog keys, the
`official` / `gate` / `canary` labels, the licence table, the example file —
lives on `docs/concepts/harness.md`, next to the example that demonstrates it.
Restating any of it here would create a second copy, and the copy is the one
that goes stale.

## 1. Two repositories, not one rename

`MolCrafts/harness` is a **new empty repository**. It is not
`MolCrafts/molcrafts-harness` renamed.

`MolCrafts/molcrafts-harness` was the plugin marketplace. It is archived or
deleted **only after cutover**, never before, and that step needs its own
authorisation. Until then it keeps its own history and its own MIT licence.

Why a rename was refused: it would carry the old marketplace layout and every
stale install instruction into the new repository's first commit; it would
leave a GitHub redirect, so a host still configured against the old address
would keep working and nobody would learn they were on it; and it would carry
the old licence across as a default, making a licensing decision by accident.

The new repository holds agent tooling only. Provider repositories (molq,
molexp, molvis, molpy) do not move into it.

## 2. Identity is a Git SHA

A harness commit is identified by its 40-character lowercase Git SHA and by
nothing else — no version number, no `latest`, no tag, no branch.

The SHA is not written into the catalog file; the caller that unpacked the tree
passes it to `load_harness_catalog`. A file stating its own SHA could disagree
with the tree it sits in, and nothing would be able to say which was wrong.
