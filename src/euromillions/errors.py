"""Errors that this package raises on purpose."""

from __future__ import annotations


class EuromillionsError(Exception):
    """A problem to report to the user, as opposed to a bug in this package."""


class HistoryError(EuromillionsError, ValueError):
    """The draw history cannot support what was asked of it."""
