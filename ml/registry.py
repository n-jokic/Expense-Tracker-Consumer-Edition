"""
ml/registry.py — ML registry (Phase 4 M1): ModelInfo + no silent activation.

Every fitted model must expose ModelInfo before it can be activated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ModelInfo:
    """Immutable record for one trained model version."""
    name: str
    version: int
    trained_rows: int
    trained_at: datetime
    dataset_fingerprint: str
    metrics: dict[str, float] = field(default_factory=dict)


# ── In-memory registry (for tests; no DB required) ─────────────────────────

_registry: list[ModelInfo] = []
_active: dict[str, ModelInfo] = {}


def register_model(info: ModelInfo, user_id: int | None = None) -> ModelInfo:
    """Record a trained model. Does not activate it."""
    if not isinstance(info, ModelInfo):
        raise TypeError("info must be ModelInfo")
    if not info.metrics:
        raise ValueError("metrics must be non-empty — no silent activation without evaluation")
    if user_id is not None:
        from db import save_ml_model
        return save_ml_model(user_id, info)
    _registry.append(info)
    return info


def get_registered(name: str | None = None, user_id: int | None = None) -> list[ModelInfo]:
    if user_id is not None:
        from db import list_ml_models
        return list_ml_models(user_id, name)
    if name is None:
        return list(_registry)
    return [m for m in _registry if m.name == name]


def clear_registry() -> None:
    _registry.clear()
    _active.clear()


def activate_model(name: str, version: int, user_id: int | None = None) -> ModelInfo:
    """Activate a registered model by name+version. Requires metrics."""
    if user_id is not None:
        from db import activate_ml_model
        return activate_ml_model(user_id, name, version)
    for m in _registry:
        if m.name == name and m.version == version:
            if not m.metrics:
                raise ValueError(f"model {name} v{version} has no metrics — cannot activate")
            _active[name] = m
            return m
    raise KeyError(f"model {name} v{version} not found")


def get_active(name: str, user_id: int | None = None) -> ModelInfo | None:
    if user_id is not None:
        from db import get_active_ml_model
        return get_active_ml_model(user_id, name)
    return _active.get(name)


def make_model_info(
    name: str,
    version: int,
    trained_rows: int,
    dataset_fingerprint: str,
    metrics: dict[str, float],
) -> ModelInfo:
    """Helper to build ModelInfo with UTC timestamp."""
    return ModelInfo(
        name=name,
        version=version,
        trained_rows=trained_rows,
        trained_at=datetime.now(timezone.utc),
        dataset_fingerprint=dataset_fingerprint,
        metrics=dict(metrics),
    )
