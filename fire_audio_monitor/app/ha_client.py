from __future__ import annotations

import logging
import os
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)
SUPERVISOR_API = "http://supervisor/core/api"


class HomeAssistantClient:
    def __init__(self, token: str | None = None, base_url: str = SUPERVISOR_API) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("SUPERVISOR_TOKEN is not available")
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def get_state(self, entity_id: str) -> str | None:
        url = f"{self.base_url}/states/{entity_id}"
        response = self.session.get(url, headers=self._headers(), timeout=10)
        if response.status_code == 404:
            LOGGER.warning("Presence entity %s was not found", entity_id)
            return None
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        state = data.get("state")
        return str(state) if state is not None else None

    def presence_gate_passes(self, entity_ids: tuple[str, ...], required_state: str) -> bool:
        for entity_id in entity_ids:
            state = self.get_state(entity_id)
            LOGGER.debug("Presence entity %s state is %s", entity_id, state)
            if state == required_state:
                return True
        return False

    def fire_event(self, event_type: str, payload: dict[str, Any]) -> None:
        safe_payload = {key: value for key, value in payload.items() if key != "token"}
        response = self.session.post(
            f"{self.base_url}/events/{event_type}",
            headers=self._headers(),
            json=safe_payload,
            timeout=10,
        )
        response.raise_for_status()
