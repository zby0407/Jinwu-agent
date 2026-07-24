"""Onboarding package.

The wizard's only package-level public entry point is :func:`run_onboard`.
Everything else lives in submodules — import directly from them:

- :mod:`JW.config.onboard.wizard` — orchestrator, ``run_onboard``,
  ``STEPS``, ``render_progress``
- :mod:`JW.config.onboard.steps` — per-step functions
- :mod:`JW.config.onboard.channels` — channel selection + setup
- :mod:`JW.config.onboard.helpers` — API-key prompt, ccproxy,
  npx/node, LaTeX, iMessage helpers
- :mod:`JW.config.onboard.style` — Rich styles + ``_checkbox_ask``
- :mod:`JW.config.onboard.validators` — input validators
- :mod:`JW.config.onboard.prompter` — ``NonInteractivePrompter``
  (CLI-answer container) + ``select_navigation_active`` / ``GoBack`` for
  keyboard nav
- :mod:`JW.config.onboard.constants` — canonical valid-value sets

This module used to re-export every symbol from every submodule for
backward compat during the initial refactor; those re-exports have since
been removed to keep the public surface narrow. New code should always
import from the submodule that owns the symbol; test code should use
``patch("jw.config.onboard.<submodule>.<name>")`` paths.
"""

from __future__ import annotations

# Sole package-level public entry. ``JW.config`` re-exports this
# (via lazy ``__getattr__``) so ``from jw.config import
# run_onboard`` keeps working — that import path is used by the CLI and is
# the only documented external API.
from .wizard import run_onboard

__all__ = ["run_onboard"]
