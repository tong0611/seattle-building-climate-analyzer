"""Plain-language explanations per building, built from a structured fact record.

``building_facts`` is the single source of truth: every number an explanation may
state comes from it, already rounded the way it should be displayed. Explainers turn
facts into text; ``faithfulness.check`` verifies the text against the same facts.

Only a template explainer exists today. An LLM explainer would implement the same
``Explainer`` protocol, receive ``building_facts(...)`` as JSON, and fall back to the
template whenever its output fails the faithfulness check.
"""

from __future__ import annotations





