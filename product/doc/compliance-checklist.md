# Lista de compliance del HR Assistant

Reglas que el código hace cumplir (ADR 0011). Con `ENVIRONMENT=production` la aplicación **no arranca** si
falla alguna de las reglas de producción; el mensaje empieza con `ENVIRONMENT=production refuses to start:` y
reúne todas las que fallan, sin mostrar ninguna clave.

## Reglas de producción (impiden arrancar)

| Regla | Dónde está en el código | Cómo se comprueba |
| --- | --- | --- |
| `MODEL_PROVIDER` no puede ser `fake` | `app/compliance.py` · `violations()` | `tests/test_compliance.py::test_production_refuses_unsafe_settings`; contrato `test_production_refuses_the_fake_model` |
| `API_KEYS` define al menos un cliente | `app/compliance.py` · `violations()` | `tests/test_compliance.py::test_production_refuses_unsafe_settings` |
| Cada clave de `API_KEYS` tiene 16 caracteres o más (el mensaje nombra al cliente, no la clave) | `app/compliance.py` · `violations()` y `MIN_KEY_LENGTH` | `tests/test_compliance.py::test_production_refuses_unsafe_settings` |
| Con `LANGSMITH_TRACING=true` (aunque falte la clave de LangSmith), `HIDE_INPUTS` y `HIDE_OUTPUTS` valen `true` (ocultar) o `redact` (redactar), nunca `false`. Refina la regla de la sesión 4, que exigía `true` (ADR 0014) | `app/compliance.py` · `violations()`; modos en `app/config.py` · `_trace_privacy()` y `tracing_requested` | `tests/test_compliance.py::test_production_refuses_unsafe_settings` y `::test_production_traces_may_be_hidden_or_redacted` |
| Una configuración de producción correcta sí arranca; desarrollo no aplica las reglas | `app/compliance.py` · `enforce()`; `app/main.py` · `create_app()` | `tests/test_compliance.py::test_a_safe_production_and_any_development_setup_start` |

## Controles relacionados (en cualquier entorno)

| Regla | Dónde está en el código | Cómo se comprueba |
| --- | --- | --- |
| `API_KEYS` mal formada impide arrancar; el error da la entrada y su largo, nunca su valor | `app/config.py` · `parse_api_keys()` | `tests/test_security.py::test_malformed_api_keys_never_show_the_key`; contrato `test_a_broken_api_keys_never_leaks_the_key` |
| `POST /ask` y `GET /metrics` exigen `X-API-Key`; sin clave o inválida → `401` | `app/main.py` · `require_client` | `tests/features/production.feature`; `tests/test_api.py::test_metrics_need_a_key`; contrato `test_asking_without_a_key_returns_401` |
| La clave se compara en tiempo constante | `app/security.py` · `KeyStore.client_for()` (`hmac.compare_digest`) | `tests/test_security.py::test_the_key_store_finds_the_client_of_a_key` |
| La clave nunca se escribe en el log ni en la configuración | `app/main.py` (auditoría sin la clave); `app/config.py` (`repr=False`) | `tests/features/production.feature` (el log no contiene la clave); `tests/test_config.py::test_security_defaults_and_invalid_values` |
| Límite por cliente: más de `RATE_LIMIT_PER_MINUTE` → `429` con `Retry-After` | `app/security.py` · `RateLimiter`; `app/main.py` · `limit_rate` | `tests/test_security.py`; `tests/features/production.feature`; contrato `test_too_many_requests_returns_429_with_retry_after` |
| Rechazos auditados (`auth_failed`, `rate_limited`) y `client` en el log y la traza | `app/main.py` · `_access_dependencies()` y `AskService.answer()` | `tests/features/production.feature`; `tests/test_tracing.py` |
| La página guarda la clave solo en `sessionStorage` y la manda en `X-API-Key` | `app/static/index.html` | `tests/test_api.py::test_the_page_keeps_the_key_in_session_storage`; `e2e/test_page.py` |
| Los secretos nunca están en el repositorio: `.env` ignorado y secretos con `sync: false` en Render | `.gitignore`, `.dockerignore`, `render.yaml` | Revisión del diff; `scripts/publish.py` exige `.env` en `.gitignore` |
| El contenedor corre sin privilegios | `Dockerfile` (`USER app`) | Trabajo `docker` del CI |
| Un secreto en la pregunta o el historial bloquea la petición (`400`) antes del modelo y se audita sin su valor | `app/privacy.py` · `scan()`; `app/main.py` · `AskService._screen()` | `tests/test_privacy_api.py::test_a_secret_blocks_the_request_before_the_model` |
| Los datos personales se redactan antes del modelo, en la respuesta y en las trazas; los del directorio nunca | `app/privacy.py`; `app/main.py` · `_screen()` y `_clean()`; `app/tracing.py` (`anonymizer`) | `tests/test_privacy.py`; `tests/test_privacy_api.py`; `tests/test_tracing.py::test_redacted_traces_keep_the_directory_and_drop_personal_data` |
