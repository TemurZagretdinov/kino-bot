"""
Compatibility module for projects that use a separate search handler file.

This repository keeps user menu, subscription retry, and search delivery in
app.handlers.user so the existing router order remains stable. Importing this
module exposes the same active router under the requested app.handlers.search
path.
"""

from app.handlers.user import router

__all__ = ("router",)
