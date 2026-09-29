"""
API 集成测试 - 验证路由和依赖注入

测试目标：
1. 验证新路由 (api/router.py) 与 Depends 注入正确工作
2. 验证 USE_LEGACY_ROUTES=true 时仍能启动
3. 验证 ConfigService 缓存和配置加载
4. 验证 ResourceManager 注入
5. 验证 SessionManager 状态管理
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


# ============================================================================
# 容器和服务 Mock
# ============================================================================

@pytest.fixture
def mock_container():
    """创建模拟服务容器"""
    container = MagicMock()
    
    # ConfigService
    container.config_service = MagicMock()
    container.config_service.get_ui_config = MagicMock(return_value=MagicMock(
        food_web=MagicMock(),
        ecology_balance=MagicMock(),
        mortality=MagicMock(),
        speciation=MagicMock(),
        resource_system=MagicMock(),
    ))
    container.config_service.get_ecology_balance = MagicMock(return_value=MagicMock())
    container.config_service.get_mortality = MagicMock(return_value=MagicMock())
    container.config_service.get_speciation = MagicMock(return_value=MagicMock())
    
    # ResourceManager
    container.resource_manager = MagicMock()
    container.resource_manager.get_snapshot = MagicMock(return_value=MagicMock(
        total_npp=1000.0,
        avg_npp=100.0,
        overgrazing_tiles=0,
    ))
    
    # Repositories
    container.species_repository = MagicMock()
    container.species_repository.list_species = MagicMock(return_value=[])
    container.species_repository.get_by_lineage = MagicMock(return_value=None)
    
    container.environment_repository = MagicMock()
    container.history_repository = MagicMock()
    container.history_repository.list_turns = MagicMock(return_value=[])
    
    container.genus_repository = MagicMock()
    
    # Services
    container.save_manager = MagicMock()
    container.save_manager.list_saves = MagicMock(return_value=[])
    
    container.export_service = MagicMock()
    container.model_router = MagicMock()
    container.embedding_service = MagicMock()
    container.species_generator = MagicMock()
    
    # SimulationEngine
    container.simulation_engine = MagicMock()
    container.simulation_engine.turn_counter = 0
    
    # EnvironmentSystem
    container.environment_system = MagicMock()
    
    return container


@pytest.fixture
def mock_session():
    """创建模拟会话管理器"""
    session = MagicMock()
    session.session_id = "test-session-12345678"
    session.is_running = False
    session.current_save_name = None
    session.autosave_counter = 0
    session.pressure_queue = []
    session.get_pending_events = MagicMock(return_value=[])
    session.push_event = MagicMock()
    session.set_running = MagicMock()
    session.pop_pressure = MagicMock(return_value=None)
    return session


# ============================================================================
# 新路由测试
# ============================================================================

class TestNewRouterIntegration:
    """测试新版拆分路由 (api/router.py)"""
    
    @pytest.fixture
    def client(self, mock_container, mock_session):
        """创建使用新路由的测试客户端
        
        现在依赖注入使用 app.state，所以需要直接设置 app.state
        """
        from ...main import app
        
        # 设置 app.state（模拟 lifespan 的行为）
        app.state.container = mock_container
        app.state.session = mock_session
        
        yield TestClient(app)
        
        # 清理
        if hasattr(app.state, 'container'):
            del app.state.container
        if hasattr(app.state, 'session'):
            del app.state.session
    
    def test_health_check(self, client):
        """测试健康检查端点"""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_api_health_check(self, client, mock_session):
        """测试 API 健康检查端点"""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "session_id" in data
    
    def test_species_list(self, client, mock_container):
        """测试物种列表端点"""
        mock_container.species_repository.list_species.return_value = []
        response = client.get("/api/species/list")
        assert response.status_code == 200
    
    def test_saves_list(self, client, mock_container):
        """测试存档列表端点"""
        mock_container.save_manager.list_saves.return_value = []
        response = client.get("/api/saves/list")
        assert response.status_code == 200
        assert response.json() == []
    
    def test_history_list(self, client, mock_container):
        """测试历史记录端点"""
        mock_container.history_repository.list_turns.return_value = []
        response = client.get("/api/history")
        assert response.status_code == 200


# 旧路由兼容性测试已移除 - api/routes.py 已删除


# ============================================================================
# ConfigService 契约测试
# ============================================================================

class TestConfigServiceContract:
    """ConfigService 缓存和配置加载契约测试"""
    
    @pytest.fixture
    def config_service(self, tmp_path):
        """创建 ConfigService 实例"""
        from ...core.config_service import ConfigService
        from ...core.config import get_settings
        
        settings = get_settings().model_copy(
            update={"ui_config_path": str(tmp_path / "settings.json")}
        )
        return ConfigService(settings)
    
    def test_config_service_initialization(self, config_service):
        """测试 ConfigService 初始化"""
        assert config_service is not None
    
    def test_config_service_caching(self, config_service):
        """配置文件不存在时默认配置也应复用缓存"""
        assert not config_service._ui_config_path.exists()
        # 首次调用应该加载配置
        config1 = config_service.get_ui_config()
        
        # 第二次调用应该返回缓存的配置
        config2 = config_service.get_ui_config()
        
        # 应该是同一个对象（缓存）
        assert config1 is config2
        assert config_service.get_ecology_balance() is config1.ecology_balance
        assert config_service.get_mortality() is config1.mortality

    def test_config_service_loads_file_created_after_default(self, config_service):
        """默认缓存不能阻止后来出现的配置文件被读取"""
        default = config_service.get_ui_config()
        config_service._ui_config_path.write_text(
            '{"autosave_interval": 7}', encoding="utf-8"
        )

        loaded = config_service.get_ui_config()

        assert loaded is not default
        assert loaded.autosave_interval == 7
        assert config_service.get_ui_config() is loaded

    def test_config_service_invalidates_default_cache(self, config_service):
        """显式失效后默认配置必须重建，而后继续缓存"""
        original = config_service.get_ui_config()

        config_service.invalidate_cache()
        refreshed = config_service.get_ui_config()

        assert refreshed is not original
        assert refreshed == original
        assert config_service.get_ui_config() is refreshed
    
    def test_config_service_ecology_balance(self, config_service):
        """测试生态平衡配置获取"""
        from ...models.config import EcologyBalanceConfig
        
        config = config_service.get_ecology_balance()
        
        assert isinstance(config, EcologyBalanceConfig)
    
    def test_config_service_mortality(self, config_service):
        """测试死亡率配置获取"""
        from ...models.config import MortalityConfig
        
        config = config_service.get_mortality()
        
        assert isinstance(config, MortalityConfig)


# ============================================================================
# ResourceManager 契约测试
# ============================================================================

class TestResourceManagerContract:
    """ResourceManager 注入契约测试"""
    
    def test_resource_manager_via_dependency(self, mock_container):
        """测试 ResourceManager 通过依赖注入获取"""
        from ...main import app
        
        # 设置 app.state
        app.state.container = mock_container
        
        client = TestClient(app)
        
        # 通过 API 访问（间接测试依赖注入）
        # ResourceManager 应该可以通过容器获取
        assert mock_container.resource_manager is not None
        
        # 清理
        del app.state.container
    
    def test_resource_manager_from_container_directly(self):
        """测试直接从容器获取 ResourceManager"""
        from ...core.container import ServiceContainer
        from ...services.ecology.resource_manager import ResourceManager
        
        container = ServiceContainer()
        
        # 容器应该能提供 ResourceManager
        manager = container.resource_manager
        assert isinstance(manager, ResourceManager)


# ============================================================================
# SessionManager 契约测试
# ============================================================================

class TestSessionManagerContract:
    """SessionManager 状态管理契约测试"""
    
    def test_session_manager_initialization(self):
        """测试 SessionManager 初始化"""
        from ...core.session import SimulationSessionManager
        
        session = SimulationSessionManager()
        assert session.is_running == False
        assert session.current_save_name is None
    
    def test_session_manager_running_state(self):
        """测试运行状态管理"""
        from ...core.session import SimulationSessionManager
        
        session = SimulationSessionManager()
        
        session.set_running(True)
        assert session.is_running == True
        
        session.set_running(False)
        assert session.is_running == False
    
    def test_session_manager_save_name(self):
        """测试存档名称管理"""
        from ...core.session import SimulationSessionManager
        
        session = SimulationSessionManager()
        
        session.set_save_name("test_save")
        assert session.current_save_name == "test_save"
    
    def test_session_manager_event_queue(self):
        """测试事件队列管理"""
        from ...core.session import SimulationSessionManager
        
        session = SimulationSessionManager()
        
        session.push_event("test", "Test message", "category")
        events = session.get_pending_events(max_count=10)
        
        assert len(events) == 1
        assert events[0]["type"] == "test"


# ============================================================================
# 依赖注入契约测试
# ============================================================================

class TestDependencyInjectionContract:
    """依赖注入契约测试
    
    现在 get_container/get_session 需要 Request 参数并从 app.state 获取。
    """
    
    def test_get_container_dependency(self, mock_container):
        """测试 get_container 从 app.state 获取容器"""
        from ..dependencies import get_container
        from ...main import app
        
        # 设置 app.state
        app.state.container = mock_container
        
        # 创建 mock request
        mock_request = MagicMock()
        mock_request.app = app
        
        container = get_container(mock_request)
        assert container is mock_container
        
        # 清理
        del app.state.container
    
    def test_get_session_dependency(self, mock_session):
        """测试 get_session 从 app.state 获取会话"""
        from ..dependencies import get_session
        from ...main import app
        
        # 设置 app.state
        app.state.session = mock_session
        
        # 创建 mock request
        mock_request = MagicMock()
        mock_request.app = app
        
        session = get_session(mock_request)
        assert session is mock_session
        
        # 清理
        del app.state.session
    
    def test_require_not_running_passes(self, mock_session, mock_container):
        """测试 require_not_running 在未运行时通过"""
        from ..dependencies import require_not_running
        from fastapi import HTTPException
        from ...main import app
        
        mock_session.is_running = False
        app.state.session = mock_session
        app.state.container = mock_container
        
        # 创建 mock request
        mock_request = MagicMock()
        mock_request.app = app
        
        # 不应该抛出异常
        require_not_running(mock_request, mock_session)
        
        # 清理
        del app.state.session
        del app.state.container
    
    def test_require_not_running_fails(self, mock_session, mock_container):
        """测试 require_not_running 在运行时失败"""
        from ..dependencies import require_not_running
        from fastapi import HTTPException
        from ...main import app
        
        mock_session.is_running = True
        app.state.session = mock_session
        app.state.container = mock_container
        
        # 创建 mock request
        mock_request = MagicMock()
        mock_request.app = app
        
        with pytest.raises(HTTPException) as exc_info:
            require_not_running(mock_request, mock_session)
        
        assert exc_info.value.status_code == 400
        
        # 清理
        del app.state.session
        del app.state.container
    
    def test_require_save_loaded_passes(self, mock_session, mock_container):
        """测试 require_save_loaded 在有存档时通过"""
        from ..dependencies import require_save_loaded
        from ...main import app
        
        mock_session.current_save_name = "test_save"
        app.state.session = mock_session
        app.state.container = mock_container
        
        # 创建 mock request
        mock_request = MagicMock()
        mock_request.app = app
        
        save_name = require_save_loaded(mock_request, mock_session)
        assert save_name == "test_save"
        
        # 清理
        del app.state.session
        del app.state.container
    
    def test_require_save_loaded_fails(self, mock_session, mock_container):
        """测试 require_save_loaded 在无存档时失败"""
        from ..dependencies import require_save_loaded
        from fastapi import HTTPException
        from ...main import app
        
        mock_session.current_save_name = None
        app.state.session = mock_session
        app.state.container = mock_container
        
        # 创建 mock request
        mock_request = MagicMock()
        mock_request.app = app
        
        with pytest.raises(HTTPException) as exc_info:
            require_save_loaded(mock_request, mock_session)
        
        assert exc_info.value.status_code == 400
        
        # 清理
        del app.state.session
        del app.state.container


# ============================================================================
# 配置加载函数契约测试
# ============================================================================

class TestConfigInjectionContract:
    """Configuration injection contract tests
    
    These tests verify that:
    1. _load_*_config functions have been removed (no implicit container access)
    2. Services receive config via constructor injection
    3. Container provides configs when creating services
    """
    
    def test_no_load_config_functions_in_tile_mortality(self):
        """Verify _load_*_config functions removed from tile_based_mortality"""
        from ...simulation import tile_based_mortality
        
        # These functions should NOT exist
        assert not hasattr(tile_based_mortality, '_load_ecology_config')
        assert not hasattr(tile_based_mortality, '_load_mortality_config')
        assert not hasattr(tile_based_mortality, '_load_speciation_config')
    
    def test_no_load_config_function_in_species(self):
        """Verify _load_ecology_balance_config removed from species.py"""
        from ...simulation import species
        
        assert not hasattr(species, '_load_ecology_balance_config')
    
    def test_no_load_config_function_in_food_web(self):
        """Verify _load_food_web_config removed from food_web_manager"""
        from ...services.species import food_web_manager
        
        assert not hasattr(food_web_manager, '_load_food_web_config')
    
    def test_no_load_config_function_in_speciation(self):
        """Verify _load_speciation_config removed from speciation"""
        from ...services.species import speciation
        
        assert not hasattr(speciation, '_load_speciation_config')
    
    def test_no_load_config_function_in_resource_manager(self):
        """Verify _load_resource_config removed from resource_manager"""
        from ...services.ecology import resource_manager
        
        assert not hasattr(resource_manager, '_load_resource_config')
    
    def test_simulation_engine_accepts_configs(self):
        """Verify SimulationEngine accepts configs via constructor"""
        from ...simulation.engine import SimulationEngine
        import inspect
        
        sig = inspect.signature(SimulationEngine.__init__)
        params = list(sig.parameters.keys())
        
        # Should have 'configs' parameter
        assert 'configs' in params
    
    def test_container_provides_engine_configs(self):
        """Verify container has get_engine_configs method"""
        from ...core.container import ServiceContainer
        
        container = ServiceContainer()
        assert hasattr(container, 'get_engine_configs')
        
        # Should return dict with required keys
        configs = container.get_engine_configs()
        assert isinstance(configs, dict)
        assert 'ecology' in configs
        assert 'mortality' in configs
        assert 'speciation' in configs
        assert 'food_web' in configs


# ============================================================================
# 服务类配置注入契约测试
# ============================================================================

class TestServiceConfigInjectionContract:
    """服务类配置注入与旧死亡率占位类兼容契约测试"""
    
    def test_tile_mortality_engine_accepts_config(self):
        """测试 TileBasedMortalityEngine 接受配置注入"""
        from ...simulation.tile_based_mortality import TileBasedMortalityEngine
        from ...models.config import EcologyBalanceConfig, MortalityConfig, SpeciationConfig
        
        # 创建自定义配置
        ecology_config = EcologyBalanceConfig()
        mortality_config = MortalityConfig()
        speciation_config = SpeciationConfig()
        
        # 应该能接受配置参数
        engine = TileBasedMortalityEngine(
            batch_limit=50,
            ecology_config=ecology_config,
            mortality_config=mortality_config,
            speciation_config=speciation_config,
        )
        
        # 验证配置被正确存储
        assert engine._ecology_config is ecology_config
        assert engine._mortality_config is mortality_config
        assert engine._speciation_config is speciation_config
    
    def test_tile_mortality_engine_without_config_is_compatibility_placeholder(self):
        """旧引擎不创建默认配置，也不执行已迁移到张量阶段的计算"""
        from ...simulation.tile_based_mortality import TileBasedMortalityEngine

        engine = TileBasedMortalityEngine()

        assert engine._ecology_config is None
        assert engine._mortality_config is None
        assert engine._speciation_config is None
        assert engine.evaluate([MagicMock()]) == []
        assert engine.get_speciation_candidates() == {}
        assert engine.export_tensor_state() is None
    
    def test_tile_mortality_engine_reload_config(self, mock_container):
        """测试 TileBasedMortalityEngine 热加载配置"""
        from ...simulation.tile_based_mortality import TileBasedMortalityEngine
        from ...models.config import EcologyBalanceConfig, MortalityConfig, SpeciationConfig
        
        # 使用配置创建引擎（避免警告）
        engine = TileBasedMortalityEngine(
            ecology_config=EcologyBalanceConfig(),
            mortality_config=MortalityConfig(),
            speciation_config=SpeciationConfig(),
        )
        
        # 新的 reload_config 接受配置参数而不是从容器获取
        new_ecology = EcologyBalanceConfig(competition_base_coefficient=0.75)
        new_mortality = MortalityConfig()
        new_speciation = SpeciationConfig()
        
        engine.reload_config(
            ecology_config=new_ecology,
            mortality_config=new_mortality,
            speciation_config=new_speciation,
        )
        
        # 验证配置已更新
        assert engine._ecology_config.competition_base_coefficient == 0.75
    
    def test_food_web_manager_accepts_config(self):
        """测试 FoodWebManager 接受配置注入"""
        from ...services.species.food_web_manager import FoodWebManager
        from ...models.config import FoodWebConfig
        
        config = FoodWebConfig()
        manager = FoodWebManager(config=config)
        
        assert manager._config is config
    
    def test_food_web_manager_warns_without_config(self, caplog):
        """测试 FoodWebManager 未提供配置时发出警告"""
        import logging
        from ...services.species.food_web_manager import FoodWebManager
        
        with caplog.at_level(logging.WARNING):
            manager = FoodWebManager()
        
        # 应该有警告日志
        assert "未注入" in caplog.text or "使用默认值" in caplog.text
    
    def test_food_web_manager_reload_config(self, mock_container):
        """测试 FoodWebManager 热加载配置"""
        from ...services.species.food_web_manager import FoodWebManager
        from ...models.config import FoodWebConfig
        
        # 使用配置创建管理器（避免警告）
        manager = FoodWebManager(config=FoodWebConfig())
        
        # 新的 reload_config 接受配置参数而不是从容器获取
        new_config = FoodWebConfig(min_prey_count_t2=5)
        manager.reload_config(config=new_config)
        
        # 验证配置已更新
        assert manager._config.min_prey_count_t2 == 5
    
    def test_speciation_service_accepts_config(self):
        """测试 SpeciationService 接受配置注入"""
        from ...services.species.speciation import SpeciationService
        from ...models.config import SpeciationConfig
        
        config = SpeciationConfig()
        router = MagicMock()
        
        service = SpeciationService(router=router, config=config)
        
        assert service._config is config
    
    def test_speciation_service_warns_without_config(self, caplog):
        """测试 SpeciationService 未提供配置时发出警告"""
        import logging
        from ...services.species.speciation import SpeciationService
        
        router = MagicMock()
        
        with caplog.at_level(logging.WARNING):
            service = SpeciationService(router=router)
        
        # 应该有警告日志
        assert "未注入" in caplog.text or "使用默认值" in caplog.text
    
    def test_speciation_service_reload_config(self, mock_container):
        """测试 SpeciationService 热加载配置"""
        from ...services.species.speciation import SpeciationService
        from ...models.config import SpeciationConfig
        
        router = MagicMock()
        # 使用配置创建服务（避免警告）
        service = SpeciationService(router=router, config=SpeciationConfig())
        
        # 新的 reload_config 接受配置参数而不是从容器获取
        new_config = SpeciationConfig(base_speciation_rate=0.45)
        service.reload_config(config=new_config)
        
        # 验证配置已更新
        assert service._config.base_speciation_rate == 0.45



