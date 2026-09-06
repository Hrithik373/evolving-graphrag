"""FastAPI application: thin, fast, and never blocking on a model call."""

from egraph.api.app import app, create_app

__all__ = ["app", "create_app"]
