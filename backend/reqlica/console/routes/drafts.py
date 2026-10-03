"""Expose draft editing, review, approval, and version activation.

Handle endpoint and supporting-file edits, removals, and change inspection.
Delegate version operations to reqlica.projects. Approval applies only to the
exact reviewed version; further edits require fresh checks and approval.
"""
