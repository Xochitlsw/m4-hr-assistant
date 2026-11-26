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
| Topes que producción no puede quitar: `MAX_BODY_BYTES` ≤ 1 MiB, `MAX_HISTORY_MESSAGES` ≤ 50, `AGENT_MAX_STEPS` ≤ 5 y `MODEL_MAX_TOKENS` ≤ 1024 (ADR 0016); `CACHE_TTL_SECONDS` ≤ 900, para que la caché del gateway no sirva respuestas de más de 15 minutos (ADR 0022) | `app/compliance.py` · `CEILINGS` | `tests/test_compliance.py::test_production_refuses_unsafe_settings` |
| `CLIENT_DAILY_BUDGET_USD` mayor que 0: el presupuesto diario por cliente no se puede apagar (ADR 0017) | `app/compliance.py` · `violations()` | `tests/test_compliance.py::test_production_refuses_unsafe_settings` |
| Un arranque negado queda auditado (`startup_refused`) con las reglas incumplidas y sin claves | `app/main.py` · `create_app()` | `tests/test_hardening.py::test_a_refused_start_is_audited_with_its_violations` |
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
| Los secretos nunca están en el repositorio: `.env` ignorado y secretos con `sync: false` en Render | `.gitignore`, `.dockerignore`, `render.yaml` (raíz) | `tests/test_secrets.py`; `tests/test_delivery.py::test_the_blueprint_builds_the_product_folder_safely`; trabajo `security` del CI (gitleaks de todo el historial) |
| Dependencias fijadas con hashes; la imagen solo instala las de ejecución | `requirements.txt`, `requirements-dev.txt`, `Dockerfile` (`--require-hashes --no-deps`) | `tests/test_delivery.py`; trabajo `docker` (sin pytest en la imagen); trabajo `security` (pip-audit) |
| El CI corre con permisos de solo lectura y acciones fijadas al SHA de su commit | `.github/workflows/ci.yml` (raíz) | `tests/test_delivery.py::test_every_action_is_pinned_to_a_commit_with_its_version` y `::test_the_product_stages_are_chained_and_run_inside_product` |
| El contenedor corre sin privilegios | `Dockerfile` (`USER app`) | Trabajo `docker` del CI |
| Una inyección en la pregunta o en cualquier mensaje del historial bloquea la petición (`400`) antes del modelo y se audita con sus reglas, sin el texto | `app/injection.py`; `app/main.py` · `AskService._screen()` | `tests/test_injection.py`; `tests/test_hardening.py` |
| Claves fallidas por origen: más de `AUTH_FAILURES_PER_MINUTE` en un minuto → `429` antes de comparar la clave; la IP no se registra (huella HMAC) | `app/security.py` · `FailureLimiter`, `client_origin`, `OriginFingerprint`; `app/main.py` · `require_client` | `tests/test_access.py`; `scripts/security_check.py` |
| Presupuesto diario por cliente → `429` hasta la medianoche UTC | `app/security.py` · `DailyBudget`; `app/main.py` · `limit_rate` | `tests/test_access.py` |
| El proceso de la app no puede escribir su código ni sus datos | `Dockerfile` (archivos de root, `USER app`) | Trabajo `docker` del CI (`touch` en `/app/app` y `/app/data` debe fallar) |
| Toda respuesta lleva cabeceras de seguridad y CSP por ruta; HSTS en producción; CORS cerrado | `app/headers.py`; `app/main.py` · `create_app()` | `tests/test_hardening.py` |
| La auditoría cubre arranque, rechazos (`401`, `413`, `422`, `429`, `400`), herramientas, datos personales y salida del modelo | `app/main.py` | `tests/test_hardening.py`; `tests/test_validation.py`; `tests/test_privacy_api.py`; `tests/features/production.feature` |
| Un secreto en la pregunta o el historial bloquea la petición (`400`) antes del modelo y se audita sin su valor | `app/privacy.py` · `scan()`; `app/main.py` · `AskService._screen()` | `tests/test_privacy_api.py::test_a_secret_blocks_the_request_before_the_model` |
| Los datos personales se redactan antes del modelo, en la respuesta y en las trazas; los del directorio nunca | `app/privacy.py`; `app/main.py` · `_screen()` y `_clean()`; `app/tracing.py` (`anonymizer`) | `tests/test_privacy.py`; `tests/test_privacy_api.py`; `tests/test_tracing.py::test_redacted_traces_keep_the_directory_and_drop_personal_data` |
