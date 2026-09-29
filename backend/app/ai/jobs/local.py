"""OpenAI-compatible local model connection, isolated from simulation state."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.simulation.v2.values import digest


@dataclass(frozen=True)
class LocalNarrativeConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    provider_name: str = "Mammoet"
    timeout: float = 120.0

    @property
    def identity(self) -> str:
        return digest({
            "provider": self.provider_name,
            "base_url": self.base_url,
            "model": self.model,
            "prompt": "local-game-narrative-zh-v1",
            "temperature": 0.6,
            "max_tokens": 2048,
        })

    @classmethod
    def load(cls) -> LocalNarrativeConfig | None:
        default = Path(__file__).resolve().parents[3] / "data" / "narrative-provider.json"
        path = Path(os.environ.get("CLADE_NARRATIVE_CONFIG", str(default))).expanduser()
        if not path.is_file():
            return None
        values = json.loads(path.read_text(encoding="utf-8"))
        if values.get("enabled", True) is False:
            return None
        base_url = values.get("base_url", "").rstrip("/")
        model = values.get("model", "")
        key = os.environ.get("CLADE_NARRATIVE_API_KEY", values.get("api_key", ""))
        if not base_url.startswith(("http://", "https://")) or not model or not isinstance(key, str):
            raise ValueError("Local narrative configuration needs base_url, model and api_key")
        return cls(base_url=base_url, model=model, api_key=key,
                   provider_name=values.get("provider_name", "Mammoet"))


class LocalCapabilityRouter:
    """Expose only the narrow text request used by ModelRouterNarrativeProvider."""

    def __init__(self, client: httpx.AsyncClient, config: LocalNarrativeConfig) -> None:
        self.client, self.config = client, config

    async def acall_capability(
        self,
        capability: str,
        messages: list[dict[str, str]],
        response_format: dict[str, object] | None = None,
    ) -> str:
        if capability != "turn_report":
            raise ValueError("Local narrative router only supports turn_report")
        instructions = (
            "\nWrite all player-facing text in simplified Chinese. This is a game: use vivid, "
            "short, accessible language. Explain the recorded change, likely pressures and "
            "its cost in 1-3 sentences. No academic lecture or invented victories, deaths, "
            "births or powers. Keep all identifier fields exactly as supplied. Return JSON only."
        )
        prepared = [dict(message) for message in messages]
        prepared[0]["content"] += instructions
        response = await self.client.post(
            self.config.base_url + "/chat/completions",
            json={
                "model": self.config.model,
                "messages": prepared,
                "temperature": 0.6,
                "max_tokens": 2048,
                "stream": False,
                "response_format": response_format or {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Local model returned no narrative text")
        return content
