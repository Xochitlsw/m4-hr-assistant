# 0015 · CI y Blueprint en la raíz, dependencias fijadas y cadena de suministro

Fecha: 2026-11-26 · Estado: aceptada · Reto de producción · Actualiza el ADR 0012

## Contexto

El ADR 0012 dejó el CI (`product/.github/workflows/ci.yml`) y el Blueprint (`product/render.yaml`) dentro de
`product/`, como si fuera la raíz de su propio repositorio. En este repositorio `product/` es una subcarpeta:
GitHub solo lee `.github/workflows/` de la raíz y Render busca `render.yaml` en la raíz, así que ni el CI ni el
despliegue habrían corrido.

Además, `requirements.txt` mezclaba ejecución y pruebas, sin versiones fijas (`>=`): la imagen instalaba pytest,
pylint y Playwright, y cada build podía traer versiones distintas a las probadas. Nada revisaba vulnerabilidades
conocidas ni secretos en el historial, y las acciones de GitHub se usaban por etiqueta (`@v5`), que su dueño puede
mover.

El sistema se presenta en dos capas: la fábrica (especificaciones, agentes con roles y las verificaciones que
juzgan cada entrega) y el agente del HR Assistant en producción. La capa 1 necesitaba una evidencia que corra
sola en cada cambio.

## Decisión

**CI y Blueprint en la raíz.**

- `.github/workflows/ci.yml` corre los pasos del producto con `defaults.run.working-directory: product`.
- `render.yaml` declara `rootDir: product`. Con eso, `dockerfilePath` y `dockerContext` son relativos a `product/`
  y por omisión valen `./Dockerfile` y `product/` (documentación de Render consultada el 2026-11-26).
- Se movieron con `git mv`, así que conservan su historia.

**Seis trabajos.** Render publica solo si todos pasan (`autoDeployTrigger: checksPass`).

| Trabajo | Qué hace |
| --- | --- |
| `style` → `unit` → `e2e` → `docker` | Igual que en el ADR 0012, encadenados con `needs`. `docker` ahora también prueba la imagen: usuario `10001`, sin pytest, `HEALTHCHECK` en `healthy` y `scripts/smoke.py` contra el contenedor |
| `factory` | Las verificaciones de la fábrica desde la raíz: `scripts/verify.py` y `verification unit`, `contract`, `qa`, `e2e` y `evaluate`. Es la capa 1 del sistema funcionando en cada cambio |
| `security` | `pip-audit` de los dos locks y `gitleaks` de todo el historial (`fetch-depth: 0`) con `.gitleaks.toml` |

**Dependencias separadas, fijadas y con hashes.**

- `requirements.in` lista las de ejecución con la versión probada.
- `requirements-dev.in` agrega las herramientas (pruebas, pylint, Playwright, PyYAML, pip-audit) y toma las de
  ejecución con las mismas versiones (`-c requirements.txt`).
- `uv pip compile --universal --python-version 3.12 --generate-hashes` produce `requirements.txt` y
  `requirements-dev.txt` con todas las dependencias, transitivas incluidas, fijadas y con hash para cada
  plataforma.
- La imagen y el CI instalan con `pip install --require-hashes --no-deps`: pip rechaza un archivo cuyo hash no
  coincida y no resuelve nada por su cuenta. La imagen solo instala `requirements.txt`.
- El `requirements.txt` de la raíz es de la fábrica y no se toca. El lock de desarrollo del producto cubre todo
  lo que la fábrica necesita, así que el trabajo `factory` también instala versiones fijadas.

**Acciones fijadas al SHA del commit**, con la versión en un comentario:

| Acción | Versión | Commit |
| --- | --- | --- |
| `actions/checkout` | v5.1.0 | `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09` |
| `actions/setup-python` | v6.3.0 | `ece7cb06caefa5fff74198d8649806c4678c61a1` |
| `gitleaks/gitleaks-action` | v2.3.9 | `ff98106e4c7b2bc287b24eaf42907196329070c7` |

Los SHA salen de `git ls-remote` (tags ligeros: el SHA es el del commit). Se mantienen las versiones mayores que ya
usaba el pipeline (checkout v5, setup-python v6, gitleaks-action v2) en su último parche; existen versiones mayores
nuevas, pero cambiar de versión mayor sin leer sus notas puede romper el pipeline.

**Mínimo privilegio en el CI.** `permissions: contents: read` para todo. Solo `security` agrega
`pull-requests: read`, porque gitleaks lee los commits del pull request.

**Secretos permitidos en gitleaks.** Solo los valores de ejemplo con formato fijo (`<nombre>-key-0123456789`,
`test-only-...`). No se excluye ninguna carpeta.

## Consecuencias

- El CI y Render funcionan en este repositorio. El ensayo local de los mismos comandos, en entornos limpios, pasó
  completo (`doc/evidence/ci-local-rehearsal.txt`): la app arranca y pasa la prueba de humo solo con el lock de
  ejecución, y la fábrica pasa con el lock de desarrollo.
- `tests/test_delivery.py` vuelve reglas estas decisiones: CI y Blueprint en la raíz, acciones por SHA, etapas
  encadenadas, trabajos `factory` y `security`, solo locks con hashes, Blueprint con `rootDir` y secretos con
  `sync: false`, imagen sin dependencias de desarrollo.
- Actualizar una dependencia es editar el `.in` y regenerar el lock con el comando de su encabezado. Después
  corren las 5 compuertas y `pip-audit`.
- gitleaks-action no pide licencia en repositorios de cuentas personales; en una organización pediría
  `GITLEAKS_LICENSE`.
- El trabajo `docker` tarda unos 30 s más, porque espera el primer `HEALTHCHECK`.

## Pendiente

- **Imagen base por digest.** `python:3.12-slim` sigue fijada por etiqueta. Fijarla por digest
  (`python:3.12-slim@sha256:...`) hace el build reproducible, pero deja de recibir parches de seguridad hasta
  actualizarla a mano o con Dependabot. Se decide con acceso a la documentación del registro.
- **Dependabot** (pip, docker y github-actions) mantendría al día los locks, los SHA y el digest. Se dejó fuera
  para no abrir pull requests automáticos durante la entrega.
