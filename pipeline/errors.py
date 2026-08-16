"""Exceptions de contrôle du pipeline."""
from __future__ import annotations


class PipelineInterrupted(Exception):
    """Base des interruptions volontaires (≠ erreur métier)."""

    def __init__(self, step: str, message: str = ""):
        self.step = step
        super().__init__(message or f"Pipeline interrompu à l'étape « {step} »")


class PipelineCancelled(PipelineInterrupted):
    """L'appelant a demandé l'arrêt (déconnexion du client HTTP, SIGTERM…)."""


class PipelineDeadlineExceeded(PipelineInterrupted):
    """Le budget de temps global de la requête est épuisé."""

    def __init__(self, step: str, deadline_s: float):
        self.deadline_s = deadline_s
        super().__init__(step, f"Budget de {deadline_s:.0f}s dépassé à l'étape « {step} »")
