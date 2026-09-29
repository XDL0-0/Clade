"""Explicit opt-in reference pipeline assembly; no import-time global services."""

from __future__ import annotations

from ..pipeline import DeterministicPipeline
from .demography import PopulationUpdateStage
from .ecology import CarryingCapacityStage, CompetitionStage, FeedingStage, HabitatSuitabilityStage
from .environment import BiomeStage, ClimateStage, GeologyStage
from .hydrology import HydrologyStage
from .metrics import MetricsStage
from .mortality import MortalityStage
from .movement import ConnectivityStage, DispersalStage, MigrationStage
from .reproduction import ReproductionStage
from .resources import PrimaryProductivityStage, ResourceRegenerationStage


def ecological_pipeline() -> DeterministicPipeline:
    """Environment through demography; later evolution changes the saved manifest."""
    return DeterministicPipeline(
        [
            ClimateStage(),
            GeologyStage(),
            HydrologyStage(),
            BiomeStage(),
            PrimaryProductivityStage(),
            ResourceRegenerationStage(),
            HabitatSuitabilityStage(),
            CarryingCapacityStage(),
            CompetitionStage(),
            FeedingStage(),
            DispersalStage(),
            MigrationStage(),
            ConnectivityStage(),
            MortalityStage(),
            ReproductionStage(),
            PopulationUpdateStage(),
            MetricsStage(),
        ]
    )


def explainable_pipeline() -> DeterministicPipeline:
    """Pressure/fitness observations and fossil lifecycle; no adaptive traits yet."""
    from .extinction import ExtinctionStage
    from .fitness import TraitFitnessGradientStage
    from .selection import SelectionPressureStage

    return DeterministicPipeline(
        [
            *ecological_pipeline().stages[:-1],
            SelectionPressureStage(),
            TraitFitnessGradientStage(),
            ExtinctionStage(after="reference_fitness"),
            MetricsStage(after="reference_extinction"),
        ]
    )
