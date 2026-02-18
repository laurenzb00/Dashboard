from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class HomeAssistantConfig:
    url: str
    token: str
    verify_ssl: bool = True
    timeout_s: float = 5.0
    master_entity_id: Optional[str] = None
    scene_all_on: Optional[str] = None
    scene_all_off: Optional[str] = None
    scene_entity_ids: Optional[List[str]] = None


def _read_json_file(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_homeassistant_config(config_path: Optional[str] = None) -> Optional[HomeAssistantConfig]:
    """Load Home Assistant config.

    Precedence:
    1) Environment variables HOMEASSISTANT_URL / HOMEASSISTANT_TOKEN
    2) JSON file (default: ./config/homeassistant.json)

    Optional env vars:
    - HOMEASSISTANT_VERIFY_SSL (0/1)
    - HOMEASSISTANT_TIMEOUT_S
    """

    workspace_default = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "homeassistant.json")
    path = config_path or workspace_default

    file_cfg = _read_json_file(path) or {}

    url = (os.environ.get("HOMEASSISTANT_URL") or file_cfg.get("url") or "").strip()
    token = (os.environ.get("HOMEASSISTANT_TOKEN") or file_cfg.get("token") or "").strip()
    if not url or not token:
        return None

    verify_ssl_raw = os.environ.get("HOMEASSISTANT_VERIFY_SSL")
    if verify_ssl_raw is None:
        verify_ssl = bool(file_cfg.get("verify_ssl", True))
    else:
        verify_ssl = str(verify_ssl_raw).strip().lower() not in ("0", "false", "no", "off")

    timeout_s = file_cfg.get("timeout_s", 5.0)
    try:
        if os.environ.get("HOMEASSISTANT_TIMEOUT_S"):
            timeout_s = float(os.environ["HOMEASSISTANT_TIMEOUT_S"])
        else:
            timeout_s = float(timeout_s)
    except Exception:
        timeout_s = 5.0

    def _opt_str(key: str) -> Optional[str]:
        val = file_cfg.get(key)
        if val is None:
            return None
        val = str(val).strip()
        return val or None

    scene_ids = file_cfg.get("scene_entity_ids")
    if isinstance(scene_ids, list):
        scene_entity_ids = [str(x).strip() for x in scene_ids if str(x).strip()]
    else:
        scene_entity_ids = None

    return HomeAssistantConfig(
        url=url.rstrip("/"),
        token=token,
        verify_ssl=verify_ssl,
        timeout_s=timeout_s,
        master_entity_id=_opt_str("master_entity_id"),
        scene_all_on=_opt_str("scene_all_on"),
        scene_all_off=_opt_str("scene_all_off"),
        scene_entity_ids=scene_entity_ids,
    )


class HomeAssistantClient:
    def __init__(self, config: HomeAssistantConfig):
        self.config = config

    @property
    def base_url(self) -> str:
        return self.config.url

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def get_states(self) -> List[Dict[str, Any]]:
        r = requests.get(
            self._url("/api/states"),
            headers=self._headers(),
            timeout=self.config.timeout_s,
            verify=self.config.verify_ssl,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return []

    def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        entity_id = str(entity_id).strip()
        if not entity_id:
            return None
        r = requests.get(
            self._url(f"/api/states/{entity_id}"),
            headers=self._headers(),
            timeout=self.config.timeout_s,
            verify=self.config.verify_ssl,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None

    def call_service(self, domain: str, service: str, data: Dict[str, Any]) -> bool:
        domain = str(domain).strip()
        service = str(service).strip()
        if not domain or not service:
            return False
        r = requests.post(
            self._url(f"/api/services/{domain}/{service}"),
            headers=self._headers(),
            data=json.dumps(data or {}),
            timeout=self.config.timeout_s,
            verify=self.config.verify_ssl,
        )
        r.raise_for_status()
        return True

    def list_scenes(self) -> List[Dict[str, str]]:
        scenes: List[Dict[str, str]] = []
        for st in self.get_states():
            try:
                entity_id = str(st.get("entity_id") or "")
                if not entity_id.startswith("scene."):
                    continue
                attrs = st.get("attributes") or {}
                friendly = str(attrs.get("friendly_name") or "").strip()
                name = friendly or entity_id
                scenes.append({"entity_id": entity_id, "name": name})
            except Exception:
                continue

        allow = self.config.scene_entity_ids
        if allow:
            allowed = set(allow)
            scenes = [s for s in scenes if s.get("entity_id") in allowed]

        scenes.sort(key=lambda s: (s.get("name") or s.get("entity_id") or "").lower())
        return scenes

    def activate_scene(self, entity_id: str) -> bool:
        entity_id = str(entity_id).strip()
        if not entity_id.startswith("scene."):
            return False
        return self.call_service("scene", "turn_on", {"entity_id": entity_id})
