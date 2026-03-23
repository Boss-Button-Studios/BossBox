"""bossbox.providers — model provider abstraction layer."""

from .base import ModelNotFoundError, ModelProvider, ProviderError, ProviderUnavailableError
from .ollama import OllamaProvider

__all__ = [
    "ModelProvider",
    "ProviderError",
    "ProviderUnavailableError",
    "ModelNotFoundError",
    "OllamaProvider",
]
