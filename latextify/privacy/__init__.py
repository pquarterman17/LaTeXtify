"""Metadata inspection and stripping across document, spreadsheet, deck,
PDF and image formats.

Public surface is :mod:`.registry`::

    from latextify.privacy import inspect_file, sanitize_file, supported_extensions

Everything else is a per-format handler. See ``plans/METADATA_PRIVACY_PLAN.md``
for the design and the deliberate non-goals (no destructive PDF flattening, no
in-place legacy-binary sanitizing).
"""

from .registry import (
    format_name,
    inspect_file,
    is_supported,
    sanitize_file,
    supported_extensions,
)
from .report import Finding, InspectReport, SanitizeReport

__all__ = [
    "Finding",
    "InspectReport",
    "SanitizeReport",
    "format_name",
    "inspect_file",
    "is_supported",
    "sanitize_file",
    "supported_extensions",
]
