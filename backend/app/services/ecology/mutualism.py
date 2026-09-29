"""Spatial, abundance-limited mutualism for the classic simulation.

Rebuilt from the current species and habitat snapshot every turn. No embeddings,
process-global partner memory, schema migration, or save-specific cache is needed.
The signed ``mutualism_benefits`` value is a report, not an extra effect to apply.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Any, Sequence


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _unit(value: Any) -> float:
    return max(0.0, min(1.0, _number(value)))


def _tokens(species: Any) -> set[str]:
    """Use declared capabilities and active organ identities, never prose embeddings."""
    tokens = {str(c).strip().lower() for c in (getattr(species, "capabilities", None) or [])}
    for field in ("organs", "evolved_organs"):
        for key, organ in (getattr(species, field, None) or {}).items():
            if not isinstance(organ, dict) or organ.get("is_active", True) is False:
                continue
            tokens.add(str(key).lower())
            tokens.update(str(organ.get(k, "")).strip().lower() for k in ("type", "name"))
            tokens.update(str(c).strip().lower() for c in organ.get("capabilities", []))
    return tokens


@dataclass(frozen=True)
class MutualismTraits:
    flowering: bool = False
    fruit: bool = False
    pollinator: bool = False
    disperser: bool = False
    pollination_dependency: float = 0.0
    dispersal_dependency: float = 0.0
    nectar_dependency: float = 0.0
    fruit_dependency: float = 0.0


def mutualism_traits(species: Any) -> MutualismTraits:
    """Old saves may have only life_form_stage; it implies flowers, not fleshy fruit."""
    tokens = _tokens(species)
    traits = getattr(species, "abstract_traits", None) or {}
    plant = _number(getattr(species, "trophic_level", 1.0), 1.0) < 2.0
    flowering = plant and (
        _number(getattr(species, "life_form_stage", 0)) >= 6
        or bool(tokens & {"flower", "flowers", "flowering", "flowering_plant", "花", "开花"})
    )
    fruit = plant and bool(tokens & {
        "fruit", "fruiting", "fruit_production", "fleshy_fruit", "berry", "berries",
        "果实", "浆果", "肉质果实", "核果", "animal_seed_dispersal", "动物传播",
    })
    pollinator = not plant and bool(tokens & {
        "pollination", "pollinator", "pollen_transport", "nectar_feeding", "nectarivory",
        "传粉", "授粉", "花蜜采食", "吸蜜", "集粉足", "吸蜜口器",
    })
    disperser = not plant and bool(tokens & {
        "seed_dispersal", "seed_disperser", "frugivory", "fruit_eating", "fruit_feeding",
        "种子传播", "种子散布", "食果", "食果性", "动物散布",
    })
    # Wind/self pollination and windborne seeds explicitly reduce partner reliance.
    independent_pollen = bool(tokens & {"wind_pollination", "self_pollination", "风媒", "风媒传粉", "自花授粉"})
    independent_seed = bool(tokens & {"wind_dispersal", "seed_wings", "风力传播", "翅果"})
    return MutualismTraits(
        flowering=flowering,
        fruit=fruit,
        pollinator=pollinator,
        disperser=disperser,
        pollination_dependency=_unit(traits.get("pollination_dependency", traits.get("传粉依赖", 0.0 if independent_pollen else 0.35))) if flowering else 0.0,
        dispersal_dependency=_unit(traits.get("seed_dispersal_dependency", traits.get("散布依赖", 0.0 if independent_seed else 0.2))) if fruit else 0.0,
        nectar_dependency=_unit(traits.get("nectar_dependency", 0.25)) if pollinator else 0.0,
        fruit_dependency=_unit(traits.get("fruit_dependency", 0.25)) if disperser else 0.0,
    )


def _compatible(provider: Any, receiver: Any, relation: str) -> bool:
    """Honor measured feeding/seed dimensions when an evolved organ supplies them.

    Missing measurements in older saves are unknown, not automatic mismatches.
    Capability matching remains mandatory at the caller.
    """
    def measurement(species: Any, keys: tuple[str, ...]) -> float:
        sources = [getattr(species, "morphology_stats", None) or {}]
        for field in ("organs", "evolved_organs"):
            for organ in (getattr(species, field, None) or {}).values():
                if isinstance(organ, dict) and organ.get("is_active", True):
                    parameters = organ.get("parameters", {})
                    if isinstance(parameters, dict):
                        sources.append(parameters)
        return max((_number(source.get(key)) for source in sources for key in keys), default=0.0)

    if relation == "pollination":
        depth = measurement(receiver, ("nectar_tube_cm", "flower_depth_cm"))
        reach = measurement(provider, ("tongue_length_cm", "proboscis_length_cm"))
        return not (depth > 0.0 and reach > 0.0 and reach < depth)
    seed = measurement(receiver, ("seed_diameter_cm",))
    gape = measurement(provider, ("gape_cm", "mouth_width_cm"))
    return not (seed > 0.0 and gape > 0.0 and gape < seed)


def build_mutualism_data(
    species_list: Sequence[Any],
    habitats: Sequence[Any],
    *,
    enabled: bool = True,
    benefit: float = 0.1,
    penalty: float = 0.15,
) -> dict[str, Any]:
    """Produce report fields plus separate, once-only execution channels.

    Reproduction/resource modifiers default to 1; mortality additions default to 0.
    At each tile a visitor's finite service budget is shared across all compatible
    plants. Scores are population-weighted coverage, so a single visitor cannot
    fully service an arbitrarily large or distant plant population.
    """
    living = {sp.lineage_code: sp for sp in species_list if getattr(sp, "status", "alive") == "alive"}
    reproduction = {code: 1.0 for code in living}
    resource = {code: 1.0 for code in living}
    mortality = {code: 0.0 for code in living}
    data: dict[str, Any] = {
        "mutualism_model": "spatial_v1",
        "mutualism_links": [],
        "mutualism_benefits": {code: 0.0 for code in living},
        "mutualism_reproduction_modifiers": reproduction,
        "mutualism_resource_modifiers": resource,
        "mutualism_mortality_modifiers": mortality,
        "mutualism_service_coverage": {},
        "mutualism_missing_partners": {},
        "mutualism_seed_dispersal": {"moved_population": 0, "routes": 0},
        "mutualism_seed_plants": {},
    }
    if not enabled:
        return data
    benefit = min(0.25, max(0.0, _number(benefit, 0.1)))
    penalty = min(0.3, max(0.0, _number(penalty, 0.15)))
    by_id = {sp.id: code for code, sp in living.items() if getattr(sp, "id", None) is not None}
    # Latest row per species/tile also tolerates historical habitat rows in old saves.
    latest: dict[tuple[str, int], Any] = {}
    for habitat in habitats:
        code = by_id.get(getattr(habitat, "species_id", None))
        tile = getattr(habitat, "tile_id", None)
        if code is None or tile is None:
            continue
        key = (code, tile)
        if key not in latest or _number(getattr(habitat, "turn_index", 0)) >= _number(getattr(latest[key], "turn_index", 0)):
            latest[key] = habitat
    populations: dict[str, dict[int, float]] = defaultdict(dict)
    for (code, tile), habitat in latest.items():
        pop = max(0.0, _number(getattr(habitat, "population", 0)))
        if pop > 0:
            populations[code][tile] = pop
    totals = {code: sum(pop.values()) for code, pop in populations.items()}
    profiles = {code: mutualism_traits(sp) for code, sp in living.items()}
    services: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    food: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    link_scores: dict[tuple[str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for relation, provider_role, receiver_role, visits in (
        ("pollination", "pollinator", "flowering", 10.0),
        ("seed_dispersal", "disperser", "fruit", 2.0),
    ):
        providers = [code for code, profile in profiles.items() if getattr(profile, provider_role) and code in totals]
        receivers = [code for code, profile in profiles.items() if getattr(profile, receiver_role) and code in totals]
        demand: dict[int, float] = defaultdict(float)
        for receiver in receivers:
            for tile, count in populations[receiver].items():
                demand[tile] += count
        competitors_by_tile: dict[int, float] = defaultdict(float)
        for provider in providers:
            for tile, count in populations[provider].items():
                competitors_by_tile[tile] += count
        for provider in providers:
            for receiver in receivers:
                if not _compatible(living[provider], living[receiver], relation):
                    continue
                for tile in populations[provider].keys() & populations[receiver].keys():
                    visitor_count = populations[provider][tile]
                    plant_count = populations[receiver][tile]
                    encounter = visitor_count / (visitor_count + 5.0)
                    service = min(1.0, visits * visitor_count / demand[tile]) * encounter
                    plant_share = plant_count / totals[receiver]
                    # Plant resource supply is shared across competing visitors too.
                    competitors = competitors_by_tile[tile]
                    feeding = min(1.0, plant_count / max(1.0, competitors * visits))
                    visitor_share = visitor_count / totals[provider]
                    score = service * plant_share
                    meal = feeding * visitor_share
                    services[receiver][relation] += score
                    food[provider][relation] += meal
                    link_scores[(provider, receiver, relation)][0] += score
                    link_scores[(provider, receiver, relation)][1] += meal
    for code, profile in profiles.items():
        # Missing spatial records are unknown rather than evidence of partner loss.
        if code not in totals:
            continue
        pollen = min(1.0, services[code]["pollination"])
        seeds = min(1.0, services[code]["seed_dispersal"])
        if profile.flowering:
            reproduction[code] *= 1.0 - profile.pollination_dependency * (1.0 - pollen) + benefit * pollen
        if profile.fruit:
            sp = living[code]
            traits = getattr(sp, "abstract_traits", None) or {}
            data["mutualism_seed_plants"][code] = {
                "habitat_type": getattr(sp, "habitat_type", "terrestrial"),
                "cold_tolerance": _unit(_number(traits.get("耐寒性", 5.0), 5.0) / 10.0),
                "heat_tolerance": _unit(_number(traits.get("耐热性", 5.0), 5.0) / 10.0),
                "drought_tolerance": _unit(_number(traits.get("耐旱性", 5.0), 5.0) / 10.0),
            }
            reproduction[code] *= 1.0 - profile.dispersal_dependency * (1.0 - seeds) + benefit * 0.5 * seeds
        nectar = min(1.0, food[code]["pollination"])
        fruit = min(1.0, food[code]["seed_dispersal"])
        if profile.pollinator:
            resource[code] += benefit * nectar - penalty * profile.nectar_dependency * (1.0 - nectar)
        if profile.disperser:
            resource[code] += benefit * fruit - penalty * profile.fruit_dependency * (1.0 - fruit)
        data["mutualism_service_coverage"][code] = {"pollination": pollen, "seed_dispersal": seeds}

    # Explicit dependencies persist in species records, so extinction penalties do
    # not disappear when links are rebuilt. Parasitism/commensalism are excluded.
    explicit_pairs: set[tuple[str, str]] = set()
    for code, sp in living.items():
        if getattr(sp, "symbiosis_type", "none") != "mutualism":
            continue
        dependencies = set(getattr(sp, "symbiotic_dependencies", None) or []) - {code}
        if not dependencies:
            continue
        coverage = 0.0
        missing_partners = []
        for partner in sorted(dependencies):
            pair = tuple(sorted((code, partner)))
            shared = populations[code].keys() & populations[partner].keys()
            satisfaction = sum(
                populations[code][tile] / max(1.0, totals.get(code, 0.0))
                * min(1.0, populations[partner][tile] / populations[code][tile])
                * populations[partner][tile] / (populations[partner][tile] + 5.0)
                for tile in shared
            ) if partner in living else 0.0
            coverage += satisfaction / len(dependencies)
            if satisfaction < 1.0:
                missing_partners.append({"species_code": partner, "availability": satisfaction, "reason": "absent" if partner not in living else "limited_or_separated"})
            # A recognized exchange already rewards these partners through its
            # reproduction/food channel; do not add a second generic bonus.
            typed = any((a == code and b == partner) or (a == partner and b == code) for a, b, _ in link_scores)
            if satisfaction > 0 and pair not in explicit_pairs and not typed:
                reverse = sum(
                    populations[partner][tile] / max(1.0, totals.get(partner, 0.0))
                    * min(1.0, populations[code][tile] / populations[partner][tile])
                    * populations[code][tile] / (populations[code][tile] + 5.0)
                    for tile in shared
                )
                resource[code] += benefit * satisfaction
                resource[partner] += benefit * reverse
                link_scores[(code, partner, "explicit_mutualism")] = [satisfaction, reverse]
                explicit_pairs.add(pair)
        if missing_partners:
            data["mutualism_missing_partners"][code] = missing_partners
        dependence = _unit(getattr(sp, "dependency_strength", 0.0))
        # Unknown habitats do not prove a living partner absent. A recorded
        # dependency on an extinct/missing species is unambiguous even then.
        if code in totals:
            mortality[code] = penalty * dependence * (1.0 - min(1.0, coverage))
        else:
            missing = sum(partner not in living for partner in dependencies) / len(dependencies)
            mortality[code] = penalty * dependence * missing
    for (provider, receiver, relation), (service, meal) in sorted(link_scores.items()):
        data["mutualism_links"].append({
            "species_a": provider,
            "species_b": receiver,
            "relationship_type": relation,
            "strength": min(1.0, service),
            "benefit_a": benefit * min(1.0, meal),
            "overlap": sum(populations[receiver][tile] for tile in populations[provider].keys() & populations[receiver].keys()) / max(1.0, totals.get(receiver, 0.0)),
            "benefit_b": benefit * min(1.0, service),
        })
    for code in living:
        reproduction[code] = max(0.0, min(1.4, reproduction[code]))
        resource[code] = max(0.7, min(1.0 + benefit, resource[code]))
        data["mutualism_benefits"][code] = max(-1.0, min(0.4, reproduction[code] - 1.0 + resource[code] - 1.0 - mortality[code]))
    return data
