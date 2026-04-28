"""Fixture package used by molmcp's introspection tests.

Do not import in production. Only on the test path via pyproject's
``pythonpath = ["src", "tests"]``.
"""

__all__ = ["greet", "Widget"]


def greet(name: str) -> str:
    """Return a greeting for ``name``.

    A function used by molmcp tests to verify ``get_signature`` and
    ``get_docstring`` work for plain functions.
    """
    return f"hello, {name}"


class Widget:
    """A small example class with a method.

    Used by molmcp tests to verify class-level introspection.
    """

    def __init__(self, weight: float):
        self.weight = weight

    def grow(self, factor: float) -> float:
        """Multiply the widget's weight by ``factor``."""
        self.weight *= factor
        return self.weight
