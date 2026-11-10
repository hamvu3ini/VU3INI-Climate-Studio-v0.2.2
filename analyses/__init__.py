"""Analyses register themselves in base.REGISTRY when imported."""
from . import drought, rainfall  # noqa: F401
from .base import REGISTRY, by_module  # noqa: F401
