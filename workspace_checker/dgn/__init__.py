"""Offline DGN V8 reading, so a library can be inspected without a Bentley product."""

from .reader import (
    Definition,
    DgnLibrary,
    EmbeddedSchema,
    is_dgn_container,
    read_libraries,
    read_library,
)

__all__ = [
    "Definition",
    "DgnLibrary",
    "EmbeddedSchema",
    "is_dgn_container",
    "read_libraries",
    "read_library",
]
