"""Workflow registry — maps intent names to Python workflow functions."""

from __future__ import annotations

from typing import Callable

from services.app_context import AppContext
from services.intent_schema import IntentName, ParsedIntent, WorkflowResult

WorkflowFunc = Callable[[ParsedIntent, AppContext], WorkflowResult]


class WorkflowRegistry:
    """Registry mapping IntentName -> workflow function.

    Usage:
        registry = WorkflowRegistry()
        registry.register(IntentName.NAVIGATE_STEP, navigate_step_workflow)
        result = registry.dispatch(parsed_intent, app_context)
    """

    def __init__(self):
        self._workflows: dict[IntentName, WorkflowFunc] = {}

    def register(self, intent: IntentName, func: WorkflowFunc) -> None:
        self._workflows[intent] = func

    def dispatch(self, intent: ParsedIntent, context: AppContext) -> WorkflowResult:
        """Look up and execute the workflow for the given intent."""
        if intent.intent == IntentName.UNKNOWN or intent.confidence < 0.4:
            return WorkflowResult(
                success=False,
                message=self._unknown_message(context),
            )

        func = self._workflows.get(intent.intent)
        if func is None:
            return WorkflowResult(
                success=False,
                message=f"No workflow registered for '{intent.intent.value}'.",
            )

        try:
            return func(intent, context)
        except Exception as e:
            return WorkflowResult(success=False, message=f"Error: {e}")

    @staticmethod
    def _unknown_message(context: AppContext) -> str:
        """Return a brief hint for unrecognized commands."""
        return "I didn't catch that. Say 'commands' for a list of commands."
