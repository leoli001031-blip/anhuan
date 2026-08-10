"""Compatibility import for the explicit P3 quarantine processor.

P3 intentionally has no background queue entry in the prototype.  Keeping
this narrow module avoids stale imports while ensuring all processing flows
through the same manager-triggered, fail-closed implementation.
"""
from .processor import process_controlled_ingestion


__all__ = ("process_controlled_ingestion",)
