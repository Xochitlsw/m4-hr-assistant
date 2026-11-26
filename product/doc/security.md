# Seguridad del HR Assistant

Controles implementados, por qué existen, dónde están en el código, cómo se prueban y qué evidencia dejan. El
detalle de cada decisión está en los ADR 0009 a 0017; las reglas que impiden arrancar mal configurado, en
[`compliance-checklist.md`](compliance-checklist.md).

La seguridad va en capas: lo que una capa no detiene, lo contiene la siguiente. Una inyección parafraseada que
pase las heurísticas sigue frente a un agente que solo tiene dos herramientas de solo lectura con argumentos
validados, un tope de pasos y una salida limpia y redactada.

## 1. Modelo de amenazas

| Actor | Qué intenta | Controles que lo frenan |
| --- | --- | --- |
| Alguien sin clave | Usar la API, adivinar claves, saturar el servicio | 7, 9, 1 (tope del cuerpo antes de leer), 15 |
| Cliente con clave (o una clave robada) | Gastar sin límite, sacar datos que no debe, abusar del modelo | 8, 10, 5, 13, 12 (todo queda auditado con su cliente) |
| Usuario que escribe instrucciones para el modelo | Que el agente ignore sus reglas, revele el prompt o llame herramientas falsas | 2, 5, 6, 13 |
| Historial falsificado por el cliente | Turnos `assistant` inventados con el protocolo del agente | 1, 2 |
| La propia persona de RH, por error | Pegar una CURP o un token en la pregunta | 3, 4 |
| Un servicio externo (proveedor del modelo, LangSmith) | Recibir datos personales o secretos | 3, 4, 18 |
| Un navegador de otro origen | Leer respuestas, meter la página en un marco, correr scripts inyectados | 15, 6 |
| La cadena de suministro | Una dependencia vulnerable o alterada, una acción de CI movida | 17 |
| Quien lee los logs | Encontrar texto de personas, claves o IP | 19, 11 |

## 2. Clasificación de los datos

| Clase | Ejemplos | Tratamiento |
| --- | --- | --- |
| Datos del directorio (negocio) | id, nombre, puesto, departamento, fecha de ingreso, estado | Son el propósito del asistente: se responden y nunca se redactan |
| Datos personales sensibles | CURP, RFC, correo, teléfono, tarjeta, CLABE, NSS | Se redactan antes del modelo, en la respuesta y en las trazas; la auditoría solo cuenta tipos |
| Secretos | Claves de API, tokens, claves privadas, contraseñas | En la entrada bloquean la petición; en la salida y las trazas se redactan; solo viven en variables de entorno |
| Datos de red | IP del cliente | Solo para contar fallas de autenticación; en el log, una huella HMAC por proceso |
| Texto de la conversación | Pregunta, historial, respuesta | Nunca va al log; en las trazas, redactado u oculto |

## 3. Controles

| # | Control | Riesgo (OWASP LLM 2025) | Dónde | Cómo se prueba | Evidencia |
| --- | --- | --- | --- | --- | --- |
| 1 | Validación de entradas: tope del cuerpo (`413` antes de leerlo), pregunta de 1 a 2000 caracteres, historial de hasta 20 mensajes con tope por rol, sin caracteres de control, marcas bidireccionales ni etiquetas invisibles | LLM01, LLM10 | `app/body_limit.py`, `app/schemas.py`, `app/sanitize.py` | `tests/test_validation.py`, `tests/test_sanitize.py` | `evidence/security-check.txt` (casos de control, largo, historial y cuerpo) |
| 2 | Detección de prompt injection: 7 reglas ES/EN sobre la pregunta y cada mensaje del historial, en una copia normalizada; `400` sin llamar al modelo | LLM01, LLM07 | `app/injection.py`, `app/main.py` · `_screen` | `tests/test_injection.py`, `tests/test_hardening.py` | `security-check.txt` (inyección e historial falsificado) |
| 3 | Datos personales: se redactan como `[REDACTED:TIPO]` antes del modelo, en la respuesta y en las trazas | LLM02 | `app/privacy.py`, `app/main.py` · `_screen` y `_clean`, `app/tracing.py` | `tests/test_privacy.py`, `tests/test_privacy_api.py`, `tests/test_tracing.py` | `security-check.txt` (CURP sintética); auditoría `pii_detected` |
| 4 | Secretos en la petición: bloquean con `400` | LLM02 | `app/privacy.py` | `tests/test_privacy_api.py` | `security-check.txt` (token); auditoría `secret_blocked` |
| 5 | Validación de la salida del modelo: protocolo JSON, lista blanca de herramientas, `employee_id` con forma `EMP-0000`, departamento existente; lo inválido se rechaza y se audita | LLM05, LLM06 | `app/agent.py` | `tests/test_agent.py` | Auditoría `output_rejected` |
| 6 | Sanitización de la respuesta: sin controles ni HTML, largo máximo; la página pinta con `textContent` | LLM05 | `app/sanitize.py`, `app/static/index.html` | `tests/test_sanitize.py`, `tests/test_validation.py`, `e2e/` | Auditoría `output_sanitized` |
| 7 | Autenticación por cliente con `hmac.compare_digest`; `401` uniforme | — | `app/security.py` · `KeyStore` | `tests/features/production.feature`, contrato de la sesión 4 | `security-check.txt` (sin clave, clave equivocada) |
| 8 | Límite de peticiones por cliente (`429` + `Retry-After`) | LLM10 | `app/security.py` · `RateLimiter` | `tests/test_security.py`, `production.feature` | Auditoría `rate_limited` |
| 9 | Límite de claves fallidas por origen, antes de comparar la clave | — (fuerza bruta) | `app/security.py` · `FailureLimiter`, `client_origin` | `tests/test_access.py` | `security-check.txt` (último caso); auditoría `auth_throttled` |
| 10 | Presupuesto diario de costo por cliente | LLM10 | `app/security.py` · `DailyBudget` | `tests/test_access.py`, `tests/test_compliance.py` | Auditoría `budget_exceeded` |
| 11 | Gestión de secretos: solo variables de entorno, `.env` y `.env.*` ignorados, plantilla vacía, `sync: false` en Render, nunca en log, error ni comando | LLM02 | `app/config.py`, `.gitignore`, `render.yaml` | `tests/test_secrets.py`, `tests/test_config.py`; trabajo `security` (gitleaks) | ADR 0009; sección 6 de este documento |
| 12 | Auditoría completa: 13 acciones, siempre con `request_id` y cliente, nunca texto, claves ni IP | Transversal | `app/main.py` | `tests/test_hardening.py`, `tests/test_access.py` y los de cada control | `security-check.txt` (líneas de auditoría) |
| 13 | Mínimo privilegio (matriz de la sección 4) | LLM06 | Agente, contenedor, CI, Render, tokens | `tests/test_delivery.py`; trabajo `docker` del CI | Sección 4 |
| 14 | Compliance como código: producción no arranca insegura | LLM10, LLM02 | `app/compliance.py` | `tests/test_compliance.py`, contrato | `compliance-checklist.md`; auditoría `startup_refused` |
| 15 | Cabeceras de seguridad en toda respuesta: CSP por ruta (la página, por hash), `nosniff`, `DENY`, `no-referrer`, `no-store`, HSTS en producción; CORS cerrado | LLM05 | `app/headers.py` | `tests/test_hardening.py` | `security-check.txt` (cabeceras) |
| 16 | Errores sin fugas: `500` genérico con `request_id`; `502` si el modelo falla, sin detalles ni token | LLM02 | `app/main.py`, `app/gateway.py` | `tests/test_observability_steps.py`, `tests/test_gateway.py` | — |
| 17 | Cadena de suministro: locks con hashes, imagen solo con dependencias de ejecución y base por digest, `pip-audit`, gitleaks del historial, acciones por SHA | LLM03 | `requirements*.txt`, `Dockerfile`, `.github/workflows/ci.yml` | `tests/test_delivery.py`; trabajos `security` y `docker` | `evidence/ci-local-rehearsal.txt` |
| 18 | Trazas sin texto en claro: redactadas u ocultas; producción no acepta trazas en claro | LLM02 | `app/tracing.py`, `app/compliance.py` | `tests/test_tracing.py`, `tests/test_compliance.py` | ADR 0014 |
| 19 | Logs sin texto, claves ni IP (huella HMAC del origen) | LLM02 | `app/logs.py`, `app/main.py`, `app/security.py` · `OriginFingerprint` | `tests/test_access.py`, `production.feature`, `tests/test_privacy_api.py` | `security-check.txt` |

### Mapeo OWASP Top 10 para aplicaciones LLM (2025)

| Riesgo | Controles |
| --- | --- |
| LLM01 Prompt Injection | 1, 2, 5, 13 (aunque pase, el agente solo lee el directorio) |
| LLM02 Sensitive Information Disclosure | 3, 4, 11, 16, 18, 19 |
| LLM03 Supply Chain | 17 |
| LLM04 Data and Model Poisoning | Los datos son un archivo versionado y de solo lectura en la imagen (13); no hay entrenamiento ni ajuste |
| LLM05 Improper Output Handling | 5, 6, 15 |
| LLM06 Excessive Agency | 5, 13 (dos herramientas de solo lectura, tope de pasos) |
| LLM07 System Prompt Leakage | 2 (regla `system_prompt` y `reveal`); el prompt no tiene secretos |
| LLM08 Vector and Embedding Weaknesses | No aplica: no hay RAG ni embeddings |
| LLM09 Misinformation | El agente responde solo con datos de las herramientas; `verification evaluate` mide la calidad |
| LLM10 Unbounded Consumption | 1, 8, 10, 14 (topes de producción), `max_tokens` y timeout del modelo |

## 4. Mínimo privilegio: identidad → permisos

| Identidad | Puede | No puede | Dónde se aplica |
| --- | --- | --- | --- |
| Persona de RH en la página | Preguntar con la clave de su cliente | Ver o cambiar otra cosa; su clave vive solo en `sessionStorage` | `app/static/index.html`, `app/main.py` |
| Cliente de la API (`API_KEYS`) | `POST /ask` y `GET /metrics` | Pasar de su límite por minuto ni de su presupuesto diario; escribir datos | `app/main.py`, `app/security.py` |
| Agente (el modelo) | Llamar `get_employee` y `search_by_department` con argumentos validados, hasta `AGENT_MAX_STEPS` veces | Escribir, ejecutar código, salir a la red, usar otra herramienta | `app/agent.py` |
| Proceso de la aplicación | Leer su código y sus datos; leer sus secretos del entorno | Modificar su código o sus datos (son de root); iniciar sesión (usuario sin shell) | `Dockerfile`; trabajo `docker` (usuario `10001`, `touch` falla en `/app/app` y `/app/data`) |
| Token de Cloudflare (`CF_API_TOKEN`) | Correr modelos de Workers AI y hacer inferencia por los gateways de la cuenta (permisos: Workers AI Read y Edit, AI Gateway Run) | Ver o cambiar la configuración de los gateways, leer sus registros, administrar la cuenta u otros productos | Cloudflare (lo crea la persona). El gateway es autenticado (ADR 0008): cada llamada manda el token en `Authorization` y en `cf-aig-authorization`, así que el gateway es una capa más de control además de Workers AI |
| Clave de LangSmith (`LANGSMITH_API_KEY`) | Crear trazas y proyectos (tipo *Tracing Only*, prefijo `lsv2_tr_`) | Leer trazas, borrar o administrar | LangSmith (la crea la persona) |
| `GITHUB_TOKEN` de Actions | Leer el repositorio; `security` además lee los pull requests | Escribir código, publicar, cambiar configuración | `.github/workflows/ci.yml` (`permissions`) |
| Render | Desplegar cuando todos los trabajos del CI pasan; leer los secretos de Environment | Desplegar con el CI en rojo; guardar secretos en el repositorio | `render.yaml` (`checksPass`, `sync: false`) |
| Agentes de la fábrica (capa 1) | Escribir solo en sus carpetas | Leer `.env`; publicar sin `output/approved.txt` | `hooks/permissions.py`, `.claude/settings.json` |
| Persona operadora | Aprobar push, despliegue y PR; escribir secretos en Render | Que un agente publique o lea secretos por ella | Proceso de revisión y aprobación humana |

Hueco conocido de la capa 1: el hook de la fábrica revisa la herramienta `Bash`, pero en Windows Claude Code
también tiene la herramienta `PowerShell`, que el matcher no cubre. No se cambia porque es parte de la fábrica (el
juez). Lo compensan la regla de `settings.json` que niega leer `.env` y la revisión de cada cambio.

## 5. Datos personales y secretos

Política completa en el ADR 0014. En resumen: los datos personales se redactan antes de llegar al modelo, en la
respuesta y en las trazas; un secreto en la pregunta o el historial bloquea la petición; los datos del directorio
nunca se redactan. El detector mira una copia sin caracteres invisibles, en NFKC y con dígitos ASCII. Límites
conocidos: lo que no tiene forma reconocible (un domicilio, un nombre suelto) no se detecta, y un folio de 10 u 11
dígitos se redacta como teléfono o NSS.

## 6. Gestión y rotación de secretos

Dónde viven: en tu equipo, en `.env` (ignorado por git); en producción, en Render > Environment. Nunca en el
código, el repositorio, un comando, un chat, una captura ni el video. Para la prueba de humo, la clave se lee sin
eco (README, «Despliegue»).

Rotación (también si se sospecha una filtración):

| Secreto | Pasos |
| --- | --- |
| Clave de un cliente (`API_KEYS`) | 1) Genera una clave nueva larga (por ejemplo con `secrets.token_urlsafe(32)` de Python). 2) En Render > Environment agrega un cliente nuevo con esa clave junto al actual (los nombres y las claves no se pueden repetir). 3) Entrega la clave nueva a quien la usa. 4) Quita el cliente viejo y guarda: Render redespliega. 5) En Render Logs, `auth_failed` con motivo `invalid` muestra quién sigue usando la vieja |
| Token de Cloudflare (`CF_API_TOKEN`) | 1) En Cloudflare crea un token nuevo con los mismos permisos (Workers AI Read y Edit, AI Gateway Run). 2) Cámbialo en Render > Environment y en tu `.env`. 3) Corre la prueba de humo. 4) Revoca el token viejo en Cloudflare |
| Clave de LangSmith (`LANGSMITH_API_KEY`) | 1) Crea una clave *Tracing Only* nueva. 2) Cámbiala en Render y en `.env`. 3) Comprueba que llegan trazas. 4) Revoca la vieja |

Si un secreto se filtró: **revócalo primero**, aunque el servicio falle unos minutos, y después reemplázalo. Si
llegó a un commit, considera comprometido el historial: rotar es obligatorio y borrar el commit no basta. El
trabajo `security` del CI (gitleaks) revisa todo el historial en cada push.

## 7. Auditoría

Catálogo completo en el ADR 0016 y el 0017: `startup`, `startup_refused`, `auth_failed`, `auth_throttled`,
`rate_limited`, `budget_exceeded`, `request_rejected`, `injection_blocked`, `secret_blocked`, `pii_detected`,
`tool_call`, `output_rejected` y `output_sanitized`. Para seguir una petición, busca su `request_id` (el «Id de
soporte» de la página) en el log; para seguir un origen sospechoso dentro de un proceso, su huella `origin`.

## 8. Cómo se comprueba

- `uv run python -m verification unit`: todas las pruebas de los controles (el número de prueba de cada uno está
  en la tabla de la sección 3).
- `uv run python scripts/security_check.py <url>`, con la clave en `SMOKE_API_KEY` leída sin eco: 13 casos de
  ataque contra el servicio. Contra el producto local: `evidence/security-check.txt`, 13 de 13. Contra Render, en
  la fase final, junto con la comprobación del origen del ADR 0017.
- CI: `security` (pip-audit y gitleaks), `docker` (usuario, dependencias, escritura negada, `HEALTHCHECK`, prueba
  de humo) y `factory` (las verificaciones de la fábrica).

## 9. Fuera de alcance y riesgos aceptados

- Identidad de las personas (SSO): la API autentica clientes, no personas.
- Contadores compartidos entre réplicas: límite, presupuesto y fallas son por proceso (ADR 0017).
- Un clasificador de inyección con modelo y la detección de entidades con Presidio: mejoras con costo y latencia.
- Las claves de `API_KEYS` se guardan en claro en el entorno de Render; guardar solo su hash sería una mejora.
- Protección contra DDoS volumétrico: le corresponde a la plataforma (Render) y no a la aplicación.
- Las reglas de la fábrica sobre secretos (el hook que impide leer `.env`) revisan el texto del comando o la ruta
  pedida. Una lectura indirecta, como una búsqueda recursiva sobre una carpeta que contiene `.env`, no la
  detectan. La regla práctica es buscar solo en archivos versionados; la protección de fondo es que `.env` nunca
  entra a git y que las claves se rotan si quedan expuestas.
