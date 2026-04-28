# API reference

The public Python API of molmcp. Everything listed here is exported from the top-level `molmcp` package.

```python
from molmcp import (
    create_server,
    Provider,
    IntrospectionProvider,
    discover_providers,
    ENTRY_POINT_GROUP,
    PathSafetyMiddleware,
    ResponseLimitMiddleware,
    MissingAnnotationsError,
    validate_tool_annotations,
    run_safe,
    SubprocessResult,
    fence_untrusted,
)
```

## `create_server`

```python
def create_server(
    name: str = "molmcp",
    *,
    import_roots: Iterable[str] | None = None,
    providers: Iterable[Provider] | None = None,
    discover_entry_points: bool = True,
    enable_path_safety: bool = True,
    enable_response_limit: bool = True,
    response_limit_bytes: int = 256 * 1024,
    validate_annotations: bool = True,
    instructions: str | None = None,
) -> FastMCP
```

Build a fully configured `FastMCP` server.

| Parameter | Description |
|-----------|-------------|
| `name` | Server name advertised to MCP clients. |
| `import_roots` | Top-level package names whose source `IntrospectionProvider` may read. Empty/`None` disables it. |
| `providers` | Explicit `Provider` instances to register, in order. They run *after* auto-discovered Providers. |
| `discover_entry_points` | If `True`, auto-discover Providers via the `molmcp.providers` entry point group. |
| `enable_path_safety` | Mount `PathSafetyMiddleware`. |
| `enable_response_limit` | Mount `ResponseLimitMiddleware`. |
| `response_limit_bytes` | Per-response truncation threshold. |
| `validate_annotations` | After all Providers register, ensure every tool exposes `readOnlyHint` or `destructiveHint`. Raises `MissingAnnotationsError` on violation. |
| `instructions` | Server-level instructions string sent to clients. |

Returns a ready-to-run `FastMCP` server instance. Call `.run(transport=...)` on it to start serving.

## `Provider`

```python
class Provider(Protocol):
    name: str
    def register(self, mcp: FastMCP) -> None: ...
```

Runtime-checkable Protocol. Any class with these two members satisfies it. See **[Providers](../concepts/providers.md)**.

## `IntrospectionProvider`

```python
class IntrospectionProvider:
    name: str = "introspection"
    def __init__(self, import_roots: list[str]): ...
    def register(self, mcp: FastMCP) -> None: ...
```

The single built-in Provider. Registers seven read-only tools — `list_modules`, `list_symbols`, `get_source`, `get_docstring`, `get_signature`, `read_file`, `search_source` — bound to `import_roots`. Empty list registers nothing.

You usually don't instantiate this directly; `create_server(import_roots=[...])` does it for you.

## `discover_providers`

```python
def discover_providers() -> list[Provider]
```

Enumerate Provider instances declared via the `molmcp.providers` entry point group. Each entry point must resolve to a class; the class is instantiated with no arguments. Providers raising during instantiation are logged and skipped.

## `ENTRY_POINT_GROUP`

```python
ENTRY_POINT_GROUP: str = "molmcp.providers"
```

The entry point group name. Re-exported so downstream packages can reference it programmatically rather than hard-coding the string.

## Middleware

### `PathSafetyMiddleware`

```python
class PathSafetyMiddleware(Middleware): ...
```

Blocks `..` and NUL bytes in path-shaped arguments. See **[Middleware](../concepts/middleware.md#pathsafetymiddleware)**.

### `ResponseLimitMiddleware`

```python
class ResponseLimitMiddleware(Middleware):
    def __init__(self, max_bytes: int = 256 * 1024): ...
```

Truncates oversized text responses. See **[Middleware](../concepts/middleware.md#responselimitmiddleware)**.

### `validate_tool_annotations`

```python
def validate_tool_annotations(
    mcp: FastMCP, *, strict: bool = True
) -> list[str]
```

Walk every registered tool and check it exposes `readOnlyHint` or `destructiveHint`. Returns a list of warning strings; if `strict=True` and there's any violation, raises `MissingAnnotationsError` instead of returning.

Synchronous; safe to call from any context. `create_server` calls this internally when `validate_annotations=True`.

### `MissingAnnotationsError`

```python
class MissingAnnotationsError(RuntimeError): ...
```

Raised by `validate_tool_annotations(strict=True)` and by `create_server` when validation fails.

## Helpers

### `run_safe`

```python
def run_safe(
    cmd: list[str],
    *,
    cwd: str | Path,
    timeout: float,
    env: dict[str, str] | None = None,
    max_output_bytes: int = 1_000_000,
) -> SubprocessResult
```

Run `cmd` in `cwd` with a hard timeout, capturing bounded output. Enforces:

- `cmd` must be a list of strings — passing a string raises `TypeError`.
- `shell=True` is unreachable.
- A timeout is mandatory.
- `cwd` is validated to exist.
- Output is truncated to `max_output_bytes` per stream.

Raises:

- `TypeError` — if `cmd` is not `list[str]`.
- `FileNotFoundError` — if `cwd` does not exist.
- `subprocess.TimeoutExpired` — if the process exceeds `timeout`.

### `SubprocessResult`

```python
@dataclass(frozen=True)
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    truncated: bool
```

Frozen dataclass returned by `run_safe`. `truncated=True` iff stdout or stderr was clipped at `max_output_bytes`.

### `fence_untrusted`

```python
def fence_untrusted(content: str, label: str = "untrusted file content") -> str
```

Wrap `content` in a marked block:

```text
<!-- BEGIN <label> -->
<content>
<!-- END <label> -->
```

Use when returning raw file contents into the LLM context to flag the data as data, not instruction.

## Read next

- **[CLI reference](cli.md)** — the matching command-line surface
- **[Architecture](../concepts/architecture.md)** — how these pieces fit together
