"""
Settings module — imports development by default for local usage.
Production is selected via DJANGO_SETTINGS_MODULE env var on Render.
"""
from .development import *  # noqa: F401,F403
