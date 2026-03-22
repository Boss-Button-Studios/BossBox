"""Ollama provider — implemented in Step 5."""
from bossbox.providers.base import ModelProvider
class OllamaProvider(ModelProvider):
    def __init__(self, base_url="http://localhost:11434"):
        self.base_url = base_url
    async def complete(self, messages: list, **kwargs) -> str:
        raise NotImplementedError("Implemented in Step 5")
