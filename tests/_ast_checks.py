"""Static checks the tests that read production source as data share.

Some rules are about a habit rather than a result: "no module reads the
environment for configuration" is answered by parsing the module, not by
running it. The walk that answers it was copied into three test modules,
which meant three places to keep in step the day the rule grows a case.
It lives here once instead.

Not a fixture and not a ``conftest.py`` entry on purpose: these are plain
functions over an :mod:`ast` node, imported by name from any test module
(``tests`` is on pytest's ``pythonpath``). Nothing here imports molmcp,
touches the filesystem, or holds state.
"""

from __future__ import annotations

import ast

#: Attributes of ``os`` that hand a module the process environment.
_ENVIRONMENT_ATTRS: frozenset[str] = frozenset({"environ", "getenv"})


def reads_environment(tree: ast.AST) -> bool:
    """Whether *tree* reads ``os.environ`` or ``os.getenv`` anywhere.

    Both spellings count, and so does a bare ``getenv`` that was imported
    ``from os``: the import hides the module name, not the read.

    Args:
        tree: A parsed module — or any node — to walk.

    Returns:
        ``True`` when the walk finds a read of the environment.

    Examples:
        >>> reads_environment(ast.parse("import os\\nx = os.environ['A']"))
        True
        >>> reads_environment(ast.parse("from os import getenv\\nx = getenv('A')"))
        True
        >>> reads_environment(ast.parse("x = 1"))
        False
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ENVIRONMENT_ATTRS:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        if isinstance(node, ast.Name) and node.id == "getenv":
            return True
    return False
