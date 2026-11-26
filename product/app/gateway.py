"""Modelo real: Cloudflare Workers AI a través de AI Gateway (ADR 0008).

Usa un ``httpx.Client`` inyectado (las pruebas pasan uno con ``MockTransport`` y nunca
salen a la red). Cualquier falla se convierte en :class:`ModelUnavailable`, que la API
responde con ``502``. El token nunca aparece en los mensajes de error.
"""

import json
import re

import httpx

from app.config import ModelSettings
from app.llm import Completion

GATEWAY_BASE_URL = "https://gateway.ai.cloudflare.com/v1"
CACHE_TTL_HEADER = "cf-aig-cache-ttl"
CACHE_STATUS_HEADER = "cf-aig-cache-status"
FENCED_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ModelUnavailable(RuntimeError):
    """El modelo real no respondió o respondió algo que no se puede usar."""


def gateway_url(settings: ModelSettings) -> str:
    """URL del endpoint compatible con OpenAI de Workers AI en el gateway.

    :param settings: Configuración de modelos (cuenta y gateway)
    :type settings: app.config.ModelSettings
    :return: La URL de ``chat/completions``
    :rtype: str
    """
    gateway = settings.gateway
    return (
        f"{GATEWAY_BASE_URL}/{gateway.account_id}/{gateway.gateway_id}"
        "/workers-ai/v1/chat/completions"
    )


def build_http_client(settings: ModelSettings) -> httpx.Client:
    """Crea el cliente HTTP del modelo real, con el timeout configurado.

    :param settings: Configuración de modelos
    :type settings: app.config.ModelSettings
    :return: Un cliente que se comparte entre hilos y se cierra al apagar
    :rtype: httpx.Client
    """
    return httpx.Client(timeout=settings.gateway.timeout_seconds)


def _first_json_object(text: str) -> dict | None:
    """Primer objeto JSON dentro del texto, o ``None`` si no hay ninguno."""
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def to_protocol(content: str) -> str:
    """Convierte la salida de un modelo real al protocolo JSON del agente.

    Quita los bloques de código, toma el primer objeto JSON y, si no hay ninguno,
    entrega el texto como respuesta (``{"answer": ...}``). El agente sigue validando
    el protocolo y la lista blanca de herramientas.

    :param content: Texto que devolvió el modelo
    :type content: str
    :return: Un objeto JSON en texto
    :rtype: str
    """
    text = content.strip()
    fenced = FENCED_BLOCK.search(text)
    candidate = _first_json_object(fenced.group(1) if fenced else text)
    if candidate is None:
        candidate = {"answer": text}
    return json.dumps(candidate, ensure_ascii=False)


class GatewayModel:
    """Un modelo de Workers AI con tope de salida, temperatura y caché del gateway.

    :param name: Nombre del modelo (por ejemplo ``MODEL_SMALL``)
    :type name: str
    :param settings: Configuración de modelos
    :type settings: app.config.ModelSettings
    :param client: Cliente HTTP compartido
    :type client: httpx.Client
    """

    def __init__(self, name: str, settings: ModelSettings, client: httpx.Client):
        self.name = name
        self._settings = settings
        self._client = client
        self._url = gateway_url(settings)

    def _headers(self) -> dict:
        """Autorización y, si la caché está encendida, su TTL (palanca 4)."""
        gateway = self._settings.gateway
        headers = {"Authorization": f"Bearer {gateway.api_token}"}
        if gateway.cache_ttl_seconds > 0:
            headers[CACHE_TTL_HEADER] = str(gateway.cache_ttl_seconds)
        return headers

    def complete(self, messages: list[dict]) -> Completion:
        """Llama al gateway y devuelve la respuesta en el protocolo del agente.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: Texto, tokens de ``usage`` y si vino de la caché
        :rtype: app.llm.Completion
        :raises ModelUnavailable: Si la llamada falla o la respuesta no sirve
        """
        body = {
            "model": self.name,
            "messages": messages,
            "max_tokens": self._settings.max_tokens,
            "temperature": self._settings.temperature,
        }
        try:
            response = self._client.post(self._url, json=body, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            usage = data["usage"]
            input_tokens = int(usage["prompt_tokens"])
            output_tokens = int(usage["completion_tokens"])
            text = to_protocol(content)
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
        ) as error:
            raise ModelUnavailable(
                f"gateway call failed for {self.name}: {type(error).__name__}"
            ) from error
        cached = response.headers.get(CACHE_STATUS_HEADER, "").upper() == "HIT"
        return Completion(text, input_tokens, output_tokens, cached=cached)
