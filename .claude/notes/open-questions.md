# Open questions

- No type checker is configured (no `ty`/`mypy` in dev deps or CI).
  Intentional, or should `check` grow a type-check step?
- No coverage tooling (`pytest-cov` absent), so `mol_project.build.coverage`
  is unset. Add if coverage gating is wanted.

- **Harness overlay 会给 discovery 缓存分区，且没有保留策略。**（2026-09-07，spec 08）
  checkout 的 capability overlay 进入 `DiscoveryEngine(overlays=)` 后会改变
  `_build_identity`，于是**每个 harness SHA** 得到自己的
  `cache/profiles/<slug>/graph.db` 与 `extract_cache.analyzer_version`。
  换一次 harness ref 就多一棵图缓存树，没有任何东西回收旧的。CLAUDE.md 的
  「stranded multi-gigabyte orphan」正是在讲这个。spec 08 明确不解决。
  待定：按 SHA 数量还是按时间剪除？`molmcp cache` 子命令要不要看得见 harness 分区？
