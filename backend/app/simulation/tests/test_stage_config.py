"""Executable configuration contract; constructors only, no world/LLM execution."""

import pytest
import yaml

from ..stage_config import (
    AVAILABLE_MODES,
    ModeParameters,
    StageConfig,
    StageConfigError,
    StageLoader,
    load_mode_with_parameters,
    load_stage_config_from_yaml,
    register_stage,
    stage_registry,
)
from ..stages import BaseStage, DependencyError, StageDependency


# Explicit fixtures rather than deriving the oracle from the YAML under test.
EXPECTED_MODES = {
    "minimal": [
        ("init", 0), ("parse_pressures", 10), ("pressure_tensor", 11),
        ("fetch_species", 30), ("tiering_and_niche", 40),
        ("tensor_state_init", 49), ("tensor_ecology", 51),
        ("population_update", 90), ("tensor_metrics", 139),
        ("build_report", 140), ("tensor_state_sync", 159), ("finalize", 180),
    ],
    "standard": [
        ("init", 0), ("parse_pressures", 10), ("pressure_tensor", 11),
        ("map_evolution", 20), ("fetch_species", 30), ("food_web", 35),
        ("tiering_and_niche", 40), ("tensor_state_init", 49),
        ("tensor_ecology", 51), ("speciation_data_transfer", 86),
        ("population_update", 90), ("gene_diversity", 93),
        ("gene_activation", 95), ("speciation", 125),
        ("background_management", 130), ("tensor_metrics", 139),
        ("build_report", 140), ("save_map_snapshot", 150),
        ("tensor_state_sync", 159), ("save_population_snapshot", 160),
        ("save_history", 170), ("finalize", 180),
    ],
    "full": [
        ("init", 0), ("parse_pressures", 10), ("pressure_tensor", 11),
        ("map_evolution", 20), ("tectonic_movement", 25),
        ("fetch_species", 30), ("food_web", 35), ("tiering_and_niche", 40),
        ("tensor_state_init", 49), ("tensor_ecology", 51),
        ("speciation_data_transfer", 86), ("population_update", 90),
        ("gene_diversity", 93), ("gene_activation", 95), ("gene_flow", 100),
        ("genetic_drift", 105), ("auto_hybridization", 110),
        ("subspecies_promotion", 115), ("speciation", 125),
        ("background_management", 130), ("tensor_metrics", 139),
        ("build_report", 140), ("save_map_snapshot", 150),
        ("vegetation_cover", 155), ("tensor_state_sync", 159),
        ("save_population_snapshot", 160), ("embedding_integration", 164),
        ("embedding_hooks", 166), ("save_history", 170), ("export_data", 175),
        ("finalize", 180),
    ],
    "debug": [
        ("init", 0), ("parse_pressures", 10), ("pressure_tensor", 11),
        ("fetch_species", 30), ("tiering_and_niche", 40),
        ("tensor_state_init", 49), ("tensor_ecology", 51),
        ("population_update", 90), ("tensor_metrics", 139),
        ("build_report", 140), ("tensor_state_sync", 159),
        ("save_population_snapshot", 160), ("finalize", 180),
    ],
}


@pytest.fixture
def config_file(tmp_path):
    def write(stages=None, **root):
        if stages is not None:
            root["modes"] = {"standard": {"stages": stages}}
        path = tmp_path / "stage_config.yaml"
        path.write_text(yaml.safe_dump(root), encoding="utf-8")
        return path
    return write


@pytest.fixture
def isolated_registry(monkeypatch):
    # StageRegistry is historically a singleton; avoid leaking test plugins.
    monkeypatch.setattr(stage_registry, "_stages", stage_registry._stages.copy())
    return stage_registry


class CustomStage(BaseStage):
    def __init__(self, order=47, name="Custom display", marker=None):
        super().__init__(order=order, name=name)
        self.marker = marker

    async def execute(self, ctx, engine):
        raise AssertionError("Configuration tests must not execute simulation stages")


@pytest.mark.parametrize("mode", AVAILABLE_MODES)
def test_mode_stage_ids_and_orders_are_frozen(mode):
    stages = StageLoader().load_stages_for_mode(mode)
    assert [(stage.stage_id, stage.order) for stage in stages] == EXPECTED_MODES[mode]
    assert [(config.name, config.order) for config in load_stage_config_from_yaml(mode=mode)] == EXPECTED_MODES[mode]
    assert all(stage.stage_id not in {"resource_calculation", "ecological_realism"} for stage in stages)


def test_omitted_mode_keeps_standard_pipeline():
    stages = StageLoader().load_stages_for_mode()
    assert [(stage.stage_id, stage.order) for stage in stages] == EXPECTED_MODES["standard"]
    assert all(stage.stage_id != "embedding_hooks" for stage in stages)


def test_explicit_mode_then_yaml_default_then_standard(config_file):
    path = config_file(mode="minimal", modes={
        "minimal": {"stages": [{"name": "init"}]},
        "standard": {"stages": [{"name": "finalize"}]},
    })
    loader = StageLoader(yaml_path=path)
    assert [stage.stage_id for stage in loader.load_stages_for_mode()] == ["init"]
    assert [stage.stage_id for stage in loader.load_stages_for_mode("standard")] == ["finalize"]
    path = config_file([{"name": "init"}])
    assert StageLoader(yaml_path=path).load_stages_for_mode()[0].stage_id == "init"


@pytest.mark.parametrize("mode", ["invalid", "", "FULL"])
def test_invalid_explicit_mode_fails_instead_of_falling_back(mode):
    with pytest.raises(StageConfigError, match="Unknown mode"):
        StageLoader().load_stages_for_mode(mode)
    with pytest.raises(StageConfigError, match="Unknown mode"):
        ModeParameters.for_mode(mode)


def test_invalid_yaml_default_fails(config_file):
    path = config_file([{"name": "init"}], mode="invalid")
    with pytest.raises(StageConfigError, match="Unknown mode"):
        StageLoader(yaml_path=path).load_stages_for_mode()
    # An explicit caller selection takes priority over the YAML default.
    assert StageLoader(yaml_path=path).load_stages_for_mode("standard")[0].stage_id == "init"


@pytest.mark.parametrize("mode", AVAILABLE_MODES)
def test_loaded_parameters_match_selected_mode(mode):
    configs, params = load_mode_with_parameters(mode)
    assert [config.name for config in configs] == [name for name, _ in EXPECTED_MODES[mode]]
    assert params == ModeParameters.for_mode(mode)


@pytest.mark.parametrize("enabled", [True, False])
def test_unknown_stage_id_fails_even_when_disabled(config_file, enabled):
    path = config_file([{"name": "init"}, {"name": "missing_stage", "enabled": enabled}])
    loader = StageLoader(yaml_path=path)
    with pytest.raises(StageConfigError, match="Unknown stage ID 'missing_stage'"):
        loader.load_stages_for_mode(validate=False)
    assert loader.get_validation_errors()


@pytest.mark.parametrize("enabled", [True, False])
def test_duplicate_stage_id_fails(config_file, enabled):
    path = config_file([{"name": "init"}, {"name": "init", "enabled": enabled}])
    with pytest.raises(StageConfigError, match="Duplicate stage ID"):
        StageLoader(yaml_path=path).load_stages_for_mode()


@pytest.mark.parametrize("root", [
    {}, {"modes": {}}, {"modes": {"standard": []}},
    {"modes": {"standard": {"stages": []}}},
    {"modes": {"standard": {"stages": [{"name": "init", "enabled": False}]}}},
])
def test_incomplete_configuration_never_enables_fallback_stages(config_file, root):
    with pytest.raises(StageConfigError):
        StageLoader(yaml_path=config_file(**root)).load_stages_for_mode()


@pytest.mark.parametrize("entry", [
    "init", {}, {"name": ""}, {"name": "init", "enabled": "false"},
    {"name": "init", "order": "10"}, {"name": "init", "order": True},
    {"name": "init", "params": []}, {"name": "init", "params": {1: "value"}},
    {"name": "init", "enabeld": False},
])
def test_invalid_stage_entry_fails(config_file, entry):
    with pytest.raises(StageConfigError):
        StageLoader(yaml_path=config_file([entry])).load_stages_for_mode()


def test_missing_and_malformed_file_fail(tmp_path):
    path = tmp_path / "missing.yaml"
    with pytest.raises(StageConfigError, match="Cannot load"):
        StageLoader(yaml_path=path).load_stages_for_mode()
    for contents in ["modes: [", "[]", ""]:
        path.write_text(contents)
        with pytest.raises(StageConfigError):
            StageLoader(yaml_path=path).load_stages_for_mode()


def test_registry_id_and_display_name_are_separate(isolated_registry):
    register_stage("custom_stage")(CustomStage)
    stage = isolated_registry.create_stage("custom_stage", marker=3)
    assert isinstance(stage, CustomStage)
    assert stage.stage_id == "custom_stage"
    assert stage.name == "Custom display"
    assert stage.marker == 3
    isolated_registry.register("custom_stage", CustomStage)  # Idempotent registration.
    with pytest.raises(StageConfigError, match="already registered"):
        isolated_registry.register("custom_stage", type("OtherStage", (CustomStage,), {}))


@pytest.mark.parametrize("top_order, constructor_order, expected", [
    (None, None, 47), (None, 70, 70), (5, None, 5), (5, 70, 5),
])
def test_custom_constructor_params_and_config_order(config_file, isolated_registry, top_order, constructor_order, expected):
    isolated_registry.register("custom_stage", CustomStage)
    params = {"name": "Custom display", "marker": {"value": 3}}
    entry = {"name": "custom_stage", "params": params}
    if top_order is not None:
        entry["order"] = top_order
    if constructor_order is not None:
        params["order"] = constructor_order
    stage = StageLoader(yaml_path=config_file([entry])).load_stages_for_mode()[0]
    assert isinstance(stage, CustomStage)
    assert stage.order == expected
    assert stage.name == "Custom display"
    assert stage.stage_id == "custom_stage"
    assert stage.marker == {"value": 3}


def test_bad_constructor_parameters_fail_instead_of_skipping_stage(config_file):
    path = config_file([{"name": "init", "params": {"unsupported": True}}])
    with pytest.raises(StageConfigError, match="Cannot construct stage 'init'"):
        StageLoader(yaml_path=path).load_stages_for_mode()


def test_explicit_order_is_applied_before_dependency_validation(config_file):
    path = config_file([{"name": "init", "order": 20}, {"name": "parse_pressures", "order": 10}])
    with pytest.raises(DependencyError, match="init"):
        StageLoader(yaml_path=path).load_stages_for_mode()


def test_required_builtin_stage_cannot_be_removed(config_file):
    path = config_file([{"name": "parse_pressures"}])
    with pytest.raises(DependencyError, match="解析环境压力|回合初始化|parse_pressures"):
        StageLoader(yaml_path=path).load_stages_for_mode()


@pytest.mark.parametrize("reference", ["producer", "Localized producer"])
def test_plugin_dependencies_accept_ids_and_legacy_display_names(config_file, isolated_registry, reference):
    class Producer(CustomStage):
        def get_dependency(self):
            return StageDependency(writes_fields={"custom_payload"})

    class Consumer(CustomStage):
        def get_dependency(self):
            return StageDependency(requires_stages={reference}, requires_fields={"custom_payload"})

    isolated_registry.register("producer", Producer)
    isolated_registry.register("consumer", Consumer)
    path = config_file([
        {"name": "consumer", "order": 20},
        {"name": "producer", "order": 10, "params": {"name": "Localized producer"}},
    ])
    loader = StageLoader(yaml_path=path)
    assert [stage.stage_id for stage in loader.load_stages_for_mode()] == ["producer", "consumer"]
    assert "producer" in loader.get_dependency_graph()


def test_missing_field_dependency_still_fails(config_file, isolated_registry):
    class Consumer(CustomStage):
        def get_dependency(self):
            return StageDependency(requires_fields={"unproduced_field"})

    isolated_registry.register("consumer", Consumer)
    with pytest.raises(DependencyError, match="unproduced_field"):
        StageLoader(yaml_path=config_file([{"name": "consumer"}])).load_stages_for_mode()


def test_ambiguous_display_dependency_requires_id(config_file, isolated_registry):
    class Consumer(CustomStage):
        def get_dependency(self):
            return StageDependency(requires_stages={"Custom display"})

    isolated_registry.register("first", CustomStage)
    isolated_registry.register("second", CustomStage)
    isolated_registry.register("consumer", Consumer)
    path = config_file([{"name": "first"}, {"name": "second"}, {"name": "consumer"}])
    with pytest.raises(DependencyError, match="Ambiguous display-name"):
        StageLoader(yaml_path=path).load_stages_for_mode()


def test_validation_diagnostics_reset_after_success(config_file):
    path = config_file([{"name": "parse_pressures"}])
    loader = StageLoader(yaml_path=path)
    with pytest.raises(DependencyError):
        loader.load_stages_for_mode()
    assert loader.get_validation_errors()
    config_file([{"name": "init"}])
    loader.load_stages_for_mode()
    assert loader.get_validation_errors() == []
    assert loader.get_validation_warnings() == []


def test_code_config_factory_preserves_custom_instance():
    config = StageConfig("custom", order=9, factory=CustomStage, params={"marker": 42})
    stage = StageLoader()._create_stage(config)
    assert isinstance(stage, CustomStage)
    assert (stage.stage_id, stage.order, stage.marker) == ("custom", 9, 42)


def test_existing_plugin_constructor_remains_compatible(config_file, isolated_registry):
    from ..plugin_stages import SimpleWeatherStage

    isolated_registry.register("simple_weather", SimpleWeatherStage)
    path = config_file([{"name": "simple_weather", "params": {"trigger_chance": 0.25}}])
    stage = StageLoader(yaml_path=path).load_stages_for_mode()[0]
    assert isinstance(stage, SimpleWeatherStage)
    assert stage.order == 22
    assert stage.trigger_chance == 0.25
    assert stage.name == "简单天气"
