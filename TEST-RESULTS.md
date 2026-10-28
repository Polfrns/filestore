# Pruebas v0.7.2 — ejecutadas el 16/10/2026

## Resultado de esta entrega

| Servicio / comprobación | Resultado ejecutado |
|---|---|
| Bridge: `FILE_TEST_PYTHON=<venv>/bin/python sh ci/test.sh` | 134 pruebas Node + 9 unittest Python: PASS, 0 omitidas |
| Roko: `sh ci/test.sh` | 7 pruebas Node de política/contexto + 3 de exportación real + 8 Jest de tarjetas + 24 Jest API: PASS, 0 omitidas |
| Filestore: `python -m pytest --ds=tests.settings_sqlite -q` | 245 PASS, 0 omitidas |
| Compose v2.40.3: Roko base + override, Bridge, Filestore | `config -q`: PASS en los tres, con entorno ficticio sin credenciales |
| Parche de agents CJS/ESM | Version guard, segunda aplicación idempotente y `node --check` de ambos run: PASS |
| Mutación: quitar raíz de interacción | La prueba de exportación falla, como debe; código original restaurado |
| Mutación: saltar saneamiento debug | La prueba de privacidad falla, como debe; código original restaurado |

Total de casos de las suites: **430 superados**. Las repeticiones no se suman.
La primera ejecución exploratoria de Bridge omitió 5 casos al faltar el entorno
Python; después se instalaron las herramientas y la suite CI final ejecutó todos.
Durante el desarrollo hubo errores de fixtures/configuración que se corrigieron
antes de los resultados finales. No se cuentan como éxitos ejecuciones fallidas.

Tras el último ajuste de metadatos de modo se repitieron las pruebas afectadas:
10 casos de observabilidad de Roko y 7 de Bridge, todos PASS. Esos casos ya están
incluidos en el total anterior. Los resultados de las suites no afectadas se
conservan, sin repetir builds ajenos al cambio.

## Qué se verifica con los SDK reales

Roko ejecuta el `Run.processStream` instalado de @librechat/agents sobre un grafo
RunnableSequence de prueba. Se capturan spans OTLP y se comprueba: una raíz por
respuesta, padres existentes, callbacks en la misma traza, títulos posteriores
con ese padre, IDs deterministas, sesiones estables, usuarios separados, cierre
en error/cancelación y ejecución única del grafo. Se ejecuta en ambos modos.

Bridge recibe HTTP real con identidad HMAC y contexto W3C firmado: su exportador
real conserva el traceId y parentSpanId externos. Una cabecera W3C sin firma no
puede elegir la traza. Se verifican padres de herramientas, contenido de prueba
visible únicamente en debug y ausencia de secretos. El proveedor del test es un
adaptador de prueba; no llama a Codex/Claude externos.

Filestore prueba contexto firmado, cierre de spans, autenticación previa,
contratos de las 12 herramientas, permisos, operaciones de archivos, formatos y
las nuevas políticas de depuración. read_file sigue devolviendo el documento al
cliente, pero no lo exporta ni siquiera en debug.

Las pruebas de contenido cubren Base64 DOCX/PDF, cuerpos de código, claves,
Authorization, comandos, variables de entorno, URI con credenciales, presupuestos
de texto, ciclos y no mutación de datos. La suite de archivos genera y procesa
DOCX/PDF/código reales. Los tests de clasificación y protocolo de tarjetas
ROKO_BRIDGE_TOOL_V1 existentes siguen ejecutándose sin alterar sus contratos.

## Entorno y dependencias

Node 24.19.0, npm 11.9.0, Python 3.12.14. Se instalaron los lockfiles Node con
`npm ci --ignore-scripts` para las pruebas locales y se ejecutó explícitamente el
parche de observabilidad de agents. No se cambió ninguna versión de dependencia.
Bridge: @langfuse/otel y tracing 5.13.1, @opentelemetry/api 1.9.0,
sdk-node 0.223.0, Codex SDK 0.161.0, Claude Agent SDK 0.3.291.
Roko: LibreChat v0.8.6, @librechat/agents 3.2.35 y Langfuse 5.3.0.
Filestore: Django 6.1.1, MCP 2.2.0, ChromaDB 1.5.9 y NumPy 2.5.3;
OpenTelemetry SDK/API 1.45.0 y exportadores según requirements.txt. Se reafirmaron
los pins de OpenTelemetry después de instalar las dependencias de ChromaDB.
La suite final de 245 casos se ejecutó después de esa reafirmación.

## No ejecutado aquí

- Build completo de las imágenes Docker / frontend de producción: no hay daemon
  Docker en este entorno. Sí se utilizó el binario oficial Compose para validar
  los modelos. El build se valida en el despliegue.
- PostgreSQL de producción: los contratos de Filestore se prueban con el ajuste
  SQLite ya existente. No certifica comportamiento específico de PostgreSQL.
- Despliegue en las VM del usuario y UI de Langfuse.
- Inferencias con suscripciones reales Codex/Claude, red externa de proveedores,
  y disponibilidad/distribución real entre las tres VM.

No se usaron los .env recibidos para autenticar pruebas ni acceder a servicios.
No se empaquetan .env, credenciales, Git, node_modules ni datos de usuarios.


## Histórico v0.7.1 — no resultados de esta ejecución

# Resultados ejecutados — v0.7.1

Entorno: Linux, Node v24.19.0, npm 11.9.0, Python 3.12.14, Docker Compose v2.40.3. Dependencias obtenidas de los manifests/lockfiles de los ZIP adjuntos; sin usar implementaciones de versiones anteriores.

## Pruebas ejecutadas sobre el código final

| Grupo | Resultado | Qué protege |
|---|---:|---|
| Bridge, suite Node completa con Python habilitado | **128/128**, sin omitidas | Codex/Claude, clasificación fiable, web, workspace, archivos, streaming Chat/Responses en tiempo real, V1 original y ampliado, cierre de estados, reintentos, aislamiento, sesiones, contexto firmado, exportación OTLP real privada y rechazo de marcadores de actividad forjados en razonamiento del modelo |
| Bridge, toolkit Python original | **9/9** | DOCX/PDF y Office reales, edición acumulativa, imágenes/HEIF, archivo de código sin ejecución automática, archivos comprimidos, aislamiento de lectores, conversión y medios |
| Roko, consumidores V1 y componentes React | **8/8** | Tarjetas originales, estados, duración, cancelación, reintentos y rechazo de nombres/rutas no autorizados |
| Roko, suites SDK de telemetría y contexto Bridge | **24/24** | Ciclo de vida OTEL, privacidad de URL, compatibilidad de identidad firmada y configuración existente |
| Roko, scripts de observabilidad | **4/4** | Sesiones por usuario/conversación/tenant, aislamiento de AsyncLocalStorage, destinos autorizados y exportación por el SDK real de agents con DOCX/PDF/código de control |
| Filestore, suite completa con SQLite | **240/240** | Contratos y registro de las 12 herramientas, lectura/escritura, permisos, usuarios, autenticación, KB, contexto y privacidad de exportación, excepciones y cancelaciones |
| Correlación entre los tres repositorios | **1/1** | Roko firma; Bridge verifica identidad/sesión; Python Filestore verifica la misma firma, conserva traceId/parentSpanId/sessionId y devuelve el resultado original, incluyendo tenant distinto |

**414 pruebas superadas. 0 fallidas u omitidas en estas ejecuciones finales.** Algunas primeras ejecuciones detectaron problemas de instalación/configuración y aserciones que se corrigieron antes de repetir las pruebas. Los resultados de arriba corresponden al código entregado.

## Comandos reproducibles

Desde Bridge, después de instalar las dependencias y disponer de las bibliotecas del toolkit:

```bash
npm ci
# Usa tu Python provisionado; en Docker será el del toolkit de la imagen.
FILE_TEST_PYTHON=/ruta/a/python node --test --test-concurrency=1 --test-timeout=30000 test/*.test.mjs
python -m unittest -v test/file_tools_test.py
```

FILE_TEST_PYTHON se especificó con el Python 3.12 del entorno, que tenía python-docx, PyMuPDF, openpyxl, python-pptx, Pillow, pillow-heif y py7zr. No se marca como validado un test omitido por faltar esa variable. La ejecución final tuvo todas las pruebas habilitadas. Se usa concurrencia 1 para evitar interferencia entre pruebas que lanzan y cancelan procesos del sandbox.

Desde Roko:

```bash
npm ci
npm run observability:patch
npm run frontend
node --test scripts/observability.test.cjs scripts/otel-export.test.cjs
cd client
npx jest --runInBand --coverage=false src/components/Chat/Messages/Content/__tests__/bridgeToolRecords.test.ts src/components/Chat/Messages/Content/__tests__/BridgeToolReasoning.test.tsx
# Desde packages/api:
npx jest --runInBand --coverage=false src/utils/rokoBridge.spec.ts src/telemetry/sdk.spec.ts
```

Desde Filestore (el settings de SQLite es exclusivamente para pruebas locales):

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q --ds=tests.settings_sqlite
```

Desde la raíz del paquete completo, con las dependencias de cada repo instaladas:

```bash
node --test verification/cross-service.test.cjs
```

Además se ejecutaron satisfactoriamente:

- `npm run frontend` de Roko (todos los paquetes y frontend), compilación del API y build final de client después de los cambios correspondientes.
- Instalación limpia de Roko desde el lockfile corregido; comprobación posterior con `npm ci --ignore-scripts --no-audit --no-fund --dry-run`.
- Prueba del API compilado confirmando que comparte el contexto AsyncLocalStorage con el SDK nativo.
- Instalación del parche agents desde archivos originales de @librechat/agents 3.2.35 y segunda aplicación idempotente.
- `docker compose config -q` usando el binario oficial Compose v2.40.3 para Bridge, Roko con override, Filestore producción y Filestore tests. En todos los casos la definición es válida; los servicios tienen image o build.
- Revisión de los ZIP para excluir .env, credenciales locales, bases de datos, uploads, logs, node_modules, .git, runtime-tools, entornos virtuales, binarios de ejecución y temporales. Los valores de claves en .env.example se dejan vacíos; el despliegue debe conservar su .env privado original.

## No ejecutado

- No se han usado tus cuentas reales Codex/Claude ni se han hecho inferencias de pago/suscripción.
- No se ha accedido a tus proyectos Langfuse ni se ha probado su interfaz Sessions en producción. Sí se capturaron exportaciones del SDK real en un receptor OTLP local y se verificaron atributos/jerarquía/sesiones/ausencia de datos sensibles.
- No se ejecutaron builds ni contenedores Docker: está disponible el cliente Compose para validación de configuración, sin daemon Docker.
- No se ejecutaron /opt/test.sh ni /opt/deploy.sh: son scripts de tu runner, no estaban adjuntos.
- La suite de Filestore usó SQLite, no PostgreSQL. Los tests con PostgreSQL se ejecutan mediante docker-compose.test.yml.
- No se ejecutó toda la suite global de LibreChat ni E2E en navegador; se ejecutaron las suites relevantes y las compilaciones indicadas.

Las comprobaciones pendientes de tu entorno están en VERIFY-LANGFUSE.md y las limitaciones de instrumentación en LIMITATIONS.md.
