"""Shim de compatibilidad: la API real vive en el paquete backend.api (FastAPI).

Este módulo se mantiene solo para que `backend/main.py` no tenga que cambiar su
import (`from .api_server import iniciar_api_backend`).
"""

from .api.app import app, iniciar_api_backend

__all__ = ["app", "iniciar_api_backend"]
