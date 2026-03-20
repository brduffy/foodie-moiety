"""Agent service — orchestrates intent classification and workflow dispatch."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

from services.app_context import AppContext
from services.fallback_parser import FallbackParser
from services.intent_schema import IntentName, ParsedIntent, WorkflowResult
from services.workflow_registry import WorkflowRegistry
from services.workflows import register_all_workflows


class AgentService(QObject):
    """Main entry point for the AI agent layer.

    Owns the FallbackParser and WorkflowRegistry.
    Processes user input synchronously via regex and emits results.

    Signals:
        result_ready: Emitted on main thread with WorkflowResult.
        processing_started: Emitted when classification begins.
    """

    result_ready = Signal(object)   # WorkflowResult
    processing_started = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fallback = FallbackParser()
        self._registry = WorkflowRegistry()
        self._pending_context: AppContext | None = None

        register_all_workflows(self._registry)

    def process_input(self, text: str, context: AppContext) -> None:
        """Process user natural language input.

        Classification is synchronous (regex only). The workflow is
        dispatched immediately and result_ready is emitted.
        """
        # Strip trailing punctuation — Whisper adds sentence-ending marks
        # (periods, commas) that break regex matching and LIKE queries.
        # Also covers typed input like "how much flour?" from the command bar.
        text = text.strip().rstrip(".,!?;:")
        if not text:
            return

        self._pending_context = context
        self.processing_started.emit()

        intent = self._fallback.parse(text, active_view=context.active_view)
        log.info("Parsed: text=%r → intent=%s conf=%.2f entities=%s",
                 text, intent.intent, intent.confidence, intent.entities)
        self._on_intent_classified(intent)

    def _on_intent_classified(self, intent: ParsedIntent) -> None:
        context = self._pending_context
        if context is None:
            return
        log.info("Dispatching intent=%s entities=%s (view=%s)",
                 intent.intent, intent.entities, context.active_view)
        result = self._registry.dispatch(intent, context)
        self.result_ready.emit(result)
