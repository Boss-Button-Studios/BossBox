"""
bossbox/providers/ollama.py

Async HTTP provider for a locally-running Ollama instance.

Wire protocol
-------------
  Chat completion  POST  {base_url}/api/chat
  Health probe     GET   {base_url}/api/tags

Ollama error conventions
------------------------
  • Model not pulled  → HTTP 404, body {"error": "model 'x' not found …"}
  • Server down       → httpx.ConnectError (connection refused)
  • Slow model load   → may take many seconds; ``completion_timeout`` exists
                        for this case and defaults to 120 s.
"""

from __future__ import annotations

import httpx

from .base import ModelNotFoundError, ModelProvider, ProviderUnavailableError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "smollm:360m"          # Nano tier reference model
_HEALTH_TIMEOUT = 5.0                   # seconds — probe only
_DEFAULT_COMPLETION_TIMEOUT = 120.0     # seconds — model inference can be slow


class OllamaProvider(ModelProvider):
    """
    ModelProvider implementation for a locally-running Ollama instance.

    Parameters
    ----------
    base_url:
        Root URL of the Ollama HTTP API, e.g. ``http://localhost:11434``.
    model:
        Default model identifier used when ``complete()`` is called without
        an explicit ``model`` keyword argument.
    completion_timeout:
        Seconds to wait for a completion response.  First-call model load
        can take 10–30 s on low-VRAM hardware; 120 s is intentionally
        generous to avoid false ProviderUnavailableError on cold starts.
    """

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        completion_timeout: float = _DEFAULT_COMPLETION_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.completion_timeout = completion_timeout

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def complete(self, messages: list[dict], **kwargs) -> str:
        """
        Call the Ollama /api/chat endpoint and return the assistant reply.

        Keyword overrides
        -----------------
        model : str
            Override the provider default for this call only.
        temperature : float
        top_p : float
        max_tokens : int
            Forwarded as ``num_predict`` in the Ollama payload.

        Raises
        ------
        ProviderUnavailableError
            Ollama is not reachable, timed out, or returned an unexpected
            status or response shape.
        ModelNotFoundError
            Ollama returned HTTP 404 (model not pulled).
        """
        model = kwargs.pop("model", self.model)

        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        # Forward recognised Ollama options; ignore anything else silently.
        options: dict = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs.pop("temperature")
        if "top_p" in kwargs:
            options["top_p"] = kwargs.pop("top_p")
        if "max_tokens" in kwargs:
            # Ollama uses num_predict for token budget
            options["num_predict"] = kwargs.pop("max_tokens")
        if options:
            payload["options"] = options

        response = await self._post("/api/chat", payload, model=model)
        return self._extract_content(response, model)

    async def is_available(self) -> bool:
        """
        Return True if Ollama is reachable at the configured base URL.

        Uses a short timeout so the caller is not blocked on a missing
        service.  All network errors are caught and return False.
        """
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, path: str, payload: dict, *, model: str) -> dict:
        """
        Execute an async POST to ``{base_url}{path}`` and return the parsed
        JSON body.

        Raises ProviderUnavailableError or ModelNotFoundError as appropriate.
        """
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.completion_timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. "
                "Is the Ollama service running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                f"Ollama request timed out after {self.completion_timeout}s. "
                "The model may still be loading; try increasing completion_timeout."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"HTTP error communicating with Ollama: {exc}"
            ) from exc

        if response.status_code == 404:
            # Ollama returns 404 when the model has not been pulled.
            detail = self._parse_error_body(response)
            raise ModelNotFoundError(model=model, provider=self.provider_name, detail=detail)

        if response.status_code != 200:
            raise ProviderUnavailableError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            return response.json()
        except Exception as exc:
            raise ProviderUnavailableError(
                f"Ollama returned non-JSON body: {response.text[:200]}"
            ) from exc

    @staticmethod
    def _extract_content(data: dict, model: str) -> str:
        """
        Pull the assistant reply string from a parsed Ollama response.

        Supports both /api/chat shape  {"message": {"content": "…"}}
        and the older /api/generate shape {"response": "…"} as a fallback.

        Raises
        ------
        ProviderUnavailableError
            The response body does not match either expected shape.
        """
        if "message" in data and isinstance(data["message"], dict):
            content = data["message"].get("content", "")
            if isinstance(content, str):
                return content.strip()

        if "response" in data and isinstance(data["response"], str):
            return data["response"].strip()

        raise ProviderUnavailableError(
            f"Unexpected Ollama response shape for model '{model}': "
            f"keys present = {list(data.keys())}"
        )

    @staticmethod
    def _parse_error_body(response: httpx.Response) -> str:
        """Return the 'error' field from an error response body, or empty string."""
        try:
            return response.json().get("error", "")
        except Exception:
            return response.text[:200]
