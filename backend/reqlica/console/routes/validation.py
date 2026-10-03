"""Expose draft checks, test execution, and draft endpoint calls.

Delegate checks and testing to reqlica.validation and return their results.
Draft tests must use separate data rather than the active project's database.
"""
