/**
 * useFoodWebData Hook 测试 (React Query 版)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useFoodWebData } from "@/components/FoodWebGraph/hooks/useFoodWebData";
import type { FoodWebAnalysis, FoodWebData, SpeciesSnapshot } from "@/services/api.types";

// Mock API 模块
vi.mock("@/services/api", () => ({
  fetchFoodWeb: vi.fn(),
  fetchFoodWebAnalysis: vi.fn(),
  repairFoodWeb: vi.fn(),
}));

import { fetchFoodWeb, fetchFoodWebAnalysis, repairFoodWeb } from "@/services/api";

// 创建测试用的 QueryClient wrapper
function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
  return function QueryWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

const mockFoodWebData: FoodWebData = {
  nodes: [
    {
      id: "A",
      name: "Producer A",
      trophic_level: 1,
      population: 900,
      diet_type: "producer",
      habitat_type: "grassland",
      prey_count: 0,
      predator_count: 1,
    },
    {
      id: "B",
      name: "Herbivore B",
      trophic_level: 2,
      population: 400,
      diet_type: "herbivore",
      habitat_type: "grassland",
      prey_count: 1,
      predator_count: 0,
    },
  ],
  links: [{ source: "A", target: "B", value: 0.8, prey_name: "Producer A", predator_name: "Herbivore B" }],
  keystone_species: ["B"],
  trophic_levels: { 1: ["A"], 2: ["B"] },
  total_species: 2,
  total_links: 1,
};

const mockAnalysis: FoodWebAnalysis = {
  health_score: 0.75,
  total_species: 2,
  total_links: 1,
  orphaned_consumers: [],
  starving_species: [],
  keystone_species: ["B"],
  isolated_species: [],
  avg_prey_per_consumer: 1,
  food_web_density: 0.5,
  bottleneck_warnings: [],
};

const mockSpeciesList: SpeciesSnapshot[] = mockFoodWebData.nodes.map((node) => ({
  lineage_code: node.id,
  latin_name: node.name,
  common_name: node.name,
  population: node.id === "A" ? 1000 : 500,
  population_share: node.id === "A" ? 2 / 3 : 1 / 3,
  status: "alive",
  deaths: 0,
  death_rate: 0,
  ecological_role: node.diet_type,
  notes: [],
}));

describe("useFoodWebData", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchFoodWeb).mockResolvedValue(mockFoodWebData);
    vi.mocked(fetchFoodWebAnalysis).mockResolvedValue(mockAnalysis);
  });

  it("初始化时应加载数据", async () => {
    const { result } = renderHook(() => useFoodWebData({ speciesList: mockSpeciesList }), {
      wrapper: createWrapper(),
    });

    // 初始状态是 loading
    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(fetchFoodWeb).toHaveBeenCalled();
    expect(fetchFoodWebAnalysis).toHaveBeenCalled();
  });

  it("应正确构建图数据", async () => {
    const { result } = renderHook(() => useFoodWebData({ speciesList: mockSpeciesList }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const { graphData } = result.current;
    expect(graphData.nodes.length).toBe(2);
    expect(graphData.links.length).toBe(1);
    expect(graphData.links[0]).toEqual({
      source: "A",
      target: "B",
      value: 0.8,
      preyName: "Producer A",
      predatorName: "Herbivore B",
    });

    // 检查节点属性
    const nodeA = graphData.nodes.find((n) => n.id === "A");
    expect(nodeA?.trophicLevel).toBe(1);
    expect(nodeA?.population).toBe(1000);

    const nodeB = graphData.nodes.find((n) => n.id === "B");
    expect(nodeB?.isKeystone).toBe(true);
  });

  it("过滤模式应正确筛选节点", async () => {
    const { result } = renderHook(() => useFoodWebData({ speciesList: mockSpeciesList }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    // 筛选生产者
    act(() => {
      result.current.setFilterMode("producers");
    });

    expect(result.current.graphData.nodes.length).toBe(1);
    expect(result.current.graphData.nodes[0].id).toBe("A");
    expect(result.current.graphData.links).toEqual([]);

    // 筛选关键物种
    act(() => {
      result.current.setFilterMode("keystone");
    });

    expect(result.current.graphData.nodes.length).toBe(1);
    expect(result.current.graphData.nodes[0].id).toBe("B");
  });

  it("搜索应筛选匹配的节点", async () => {
    const { result } = renderHook(() => useFoodWebData({ speciesList: mockSpeciesList }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    act(() => {
      result.current.setSearchQuery("Herbivore");
    });

    expect(result.current.graphData.nodes.length).toBe(1);
    expect(result.current.graphData.nodes[0].name).toBe("Herbivore B");
  });

  it("加载失败时应设置错误状态", async () => {
    vi.mocked(fetchFoodWeb).mockRejectedValue(new Error("Network error"));

    const { result } = renderHook(() => useFoodWebData({ speciesList: mockSpeciesList }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe("Network error");
  });

  it("修复功能应调用 API 并刷新数据", async () => {
    vi.mocked(repairFoodWeb).mockResolvedValue({
      repaired_count: 2,
      changes: [],
      analysis_after: { health_score: 1, orphaned_consumers: 0, starving_species: 0 },
    });

    const { result } = renderHook(() => useFoodWebData({ speciesList: mockSpeciesList }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.repair();
    });

    expect(repairFoodWeb).toHaveBeenCalled();
  });
});
