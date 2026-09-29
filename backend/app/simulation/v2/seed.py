"""Versioned, stateless random streams, independent of stage/worker execution order."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from .values import canonical_bytes, natural


@dataclass(frozen=True, slots=True)
class SeedStream:
    key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key, (bytes, bytearray)) or len(self.key) != 32:
            raise ValueError("A stream key must contain 32 bytes")
        object.__setattr__(self, "key", bytes(self.key))

    def uint64(self, counter: int, *, attempt: int = 0) -> int:
        natural(counter, "counter")
        natural(attempt, "attempt")
        value = hashlib.blake2b(
            canonical_bytes(["clade-rng-v1", self.key.hex(), counter, attempt]),
            digest_size=8,
        ).digest()
        return int.from_bytes(value, "big")

    def uniform(self, counter: int) -> float:
        return (self.uint64(counter) >> 11) * (1.0 / (1 << 53))

    def randint(self, counter: int, low: int, high: int) -> int:
        """Uniform integer in [low, high), with rejection rather than modulo bias."""
        if any(isinstance(n, bool) or not isinstance(n, int) for n in (low, high)):
            raise ValueError("Integer range bounds must be integers")
        if high <= low or high - low > 1 << 64:
            raise ValueError("Invalid integer range")
        width = high - low
        limit = (1 << 64) - (1 << 64) % width
        attempt = 0
        while (value := self.uint64(counter, attempt=attempt)) >= limit:
            attempt += 1
        return low + value % width

    def normal(self, counter: int) -> float:
        # Box-Muller: integer sampling is exact; floating transforms are backend-versioned.
        u = ((self.uint64(counter, attempt=1) >> 11) + 1) / ((1 << 53) + 1)
        return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * self.uniform(counter))

    @property
    def seed(self) -> int:
        return int.from_bytes(self.key, "big")


@dataclass(frozen=True, slots=True)
class SeedManager:
    world_seed: int
    rng_namespace: str
    turn_id: int
    algorithm: str = "clade-blake2b-counter-v1"

    def __post_init__(self) -> None:
        natural(self.world_seed, "world_seed")
        natural(self.turn_id, "turn_id")
        if (
            not isinstance(self.rng_namespace, str)
            or not self.rng_namespace.strip()
            or self.algorithm != "clade-blake2b-counter-v1"
        ):
            raise ValueError("Missing RNG namespace or unsupported RNG algorithm")

    def stream(
        self,
        stage_name: str,
        stage_version: str,
        *,
        entity: str = "world",
        purpose: str = "default",
    ) -> SeedStream:
        if any(
            not isinstance(v, str) or not v.strip()
            for v in (stage_name, stage_version, entity, purpose)
        ):
            raise ValueError("A stream requires stage/version/entity/purpose identities")
        key = hashlib.blake2b(
            canonical_bytes(
                [
                    self.algorithm,
                    self.world_seed,
                    self.rng_namespace,
                    self.turn_id,
                    stage_name,
                    stage_version,
                    entity,
                    purpose,
                ]
            ),
            digest_size=32,
        ).digest()
        return SeedStream(key)
