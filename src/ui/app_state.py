from __future__ import annotations

from typing import Callable, Dict, List, Optional
import logging


class AppState:
    """Simple in-memory app state with pub/sub updates."""

    def __init__(self, validator: Optional[Callable[[dict], list[str]]] = None) -> None:
        self._data: Dict[str, object] = {}
        self._listeners: List[Callable[[dict], None]] = []
        self._validator = validator

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        if callback not in self._listeners:
            self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def update(self, payload: Optional[dict]) -> None:
        if not payload:
            return
        if self._validator:
            warnings = self._validator(payload)
            for warning in warnings:
                logging.warning("AppState payload warning: %s", warning)
        self._data.update(payload)
        snapshot = dict(self._data)
        for listener in list(self._listeners):
            try:
                listener(snapshot)
            except Exception:
                logging.exception("AppState listener failed")

    def get(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    @property
    def data(self) -> dict:
        return dict(self._data)
