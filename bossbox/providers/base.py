"""Abstract model provider base — implemented in Step 5."""
class ModelProvider:
    async def complete(self, messages: list, **kwargs) -> str:
        raise NotImplementedError("Implemented in Step 5")
class ProviderUnavailableError(Exception): pass
class ModelNotFoundError(Exception): pass
