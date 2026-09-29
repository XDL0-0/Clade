import type { Snapshot, SpeciesDetail, Timeline } from "../types";
export function snapshot(turn = 0, timeline = "control", world = "world-37"): Snapshot {
  return {
    version: { world_id: world, timeline_id: timeline, generation: 0, revision: turn },
    turn,
    snapshot_id: `snapshot-${timeline}-${turn}`,
    state_hash: `hash-${turn}`,
    model: "ecology-reference-v1",
    geometry: { width: 4, height: 2, topology: "odd-q-cylinder-v1" },
    environment: { warming_offset: 0, global_temperature: 15 },
    species: [
      {
        species_id: "grazer",
        population: 80 + turn,
        slot: 0,
        status: "Healthy",
        role: "herbivore",
        habitat: "land",
        body_mass: 2,
        traits: {
          armor: 0.1,
          speed: 0.1,
          attack: 0.2,
          cooperation: 0.2,
          toxin: 0.1,
          detox: 0.1,
          engineering: 0.2,
        },
        ancestor: null,
        descendants: [],
        created_turn: 0,
        declining_turns: 0,
        extinction_turn: null,
        extinction_cause: null,
        last_nonzero_population: 80,
        trait_budget: 3,
      },
    ],
    map: {
      biome: [4, 4, 0, 0, 4, 4, 0, 0],
      temperature: Array(8).fill(15),
      elevation: Array(8).fill(12),
      plant_biomass: Array(8).fill(200),
    },
  };
}
export const detail = (value: Snapshot): SpeciesDetail => ({
  ...value,
  species: value.species[0],
  distribution: [20, 20, 0, 0, 20, 20, 0, 0],
  fossil: {},
  evolution_traces: [],
});
export const timeline = (value: Snapshot): Timeline => ({
  world_id: value.version.world_id,
  timeline_id: value.version.timeline_id,
  version: value.version,
  turn: value.turn,
  state_hash: value.state_hash,
  model: value.model,
});
export const response = (value: unknown, status = 200) =>
  ({ ok: status < 400, status, json: async () => value }) as Response;
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, Set<EventListener>>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(kind: string, listener: EventListener) {
    if (!this.listeners.has(kind)) this.listeners.set(kind, new Set());
    this.listeners.get(kind)!.add(listener);
  }
  removeEventListener(kind: string, listener: EventListener) {
    this.listeners.get(kind)?.delete(listener);
  }
  close() {
    this.closed = true;
  }
  emit(kind: string, cursor = "17") {
    const event = new MessageEvent(kind, {
      lastEventId: cursor,
      data: JSON.stringify({
        cursor: Number(cursor),
        message_id: `message-${cursor}`,
        kind,
        payload: {},
      }),
    });
    this.listeners.get(kind)?.forEach((listener) => listener(event));
  }
}
