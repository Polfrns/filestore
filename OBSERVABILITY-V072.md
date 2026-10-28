# Observabilidad v0.7.2 — una traza por interacción

Base exclusiva: bridge(2).zip, Roko(4).zip y server(3).zip recibidos el 16/10/2026.
Esta entrega conserva los pipelines y los scripts CI existentes, las imágenes por
SHA256, el socket Docker de la VM y la red externa roko-cli-bridge.

## Comportamiento

Cada ejecución del streaming del agente crea `roko.interaction`, con el último
mensaje humano como input y la última respuesta del agente como output.
`AgentRun`, callbacks LangGraph/LangChain y llamadas Bridge/MCP ocurridas dentro
se agrupan debajo. No se fabrican operaciones ni tool_calls. Los títulos lanzados
después de iniciar ese streaming reutilizan su contexto, incluso tras cerrar la
observación raíz. Los IDs deterministas de respuesta usados por feedback se
conservan. Cada nuevo mensaje tiene su propio traceId y comparte sessionId con
los otros mensajes de esa conversación; usuarios y tenants siguen separados.

La raíz mide el streaming del agente, no el tiempo de red del navegador ni toda
la preparación HTTP previa. Trabajos ajenos a una interacción, un título que se
inicie antes de que exista su contexto, llamadas directas/stdio o servicios sin
firma válida conservan trazas independientes. Una reanudación del mismo mensaje
puede añadir otra observación de streaming a la misma traza de respuesta.

## Preparación en las VM, antes del push

No reemplaces tus `.env` por `.env.example`. Edita los `.env` privados existentes.
No cambies CLAUDE_BRIDGE_KEY / BRIDGE_API_KEY: mantienen la identidad y las sesiones.

En Roko y Bridge usa la misma URL y claves del proyecto Langfuse Roko. Configura
el mismo OBSERVABILITY_TRACE_SECRET aleatorio (al menos 32 caracteres) en los tres
servicios. Si ya existe, consérvalo. Para generar uno nuevo: `openssl rand -hex 32`.
No lo subas a Git ni lo pegues en el chat. La firma de trazado no sustituye la
autenticación del Bridge ni los permisos del MCP.

En `/home/rokoface/services/roko/.env` verifica:

```dotenv
ROKO_BRIDGE_BASE_URL=http://cli-bridge:4000/v1
OBSERVABILITY_TRACE_SECRET=<secreto compartido existente>
OBSERVABILITY_CONTENT_MODE=redacted
OBSERVABILITY_CONTENT_MAX_CHARS=4000
```

Para Filestore añade su URL HTTP REAL a OBSERVABILITY_TRACE_TARGETS, incluyendo
su ruta MCP tal como está configurada. Ejemplo orientativo, sustituye la IP:

```dotenv
OBSERVABILITY_TRACE_TARGETS=http://cli-bridge:4000/v1,http://IP_VM_FILESTORE:8000/mcp
```

La URL fija del Bridge indicada en ROKO_BRIDGE_BASE_URL queda autorizada para
propagación; Filestore necesita su entrada explícita. No uses dominios externos
ni permitas que el cliente seleccione estas variables. Cabeceras arbitrarias o
contextos firmados para otro usuario no cambian la traza del Bridge.

En `/home/rokoface/services/bridge/.env` y en el `.env` de la VM separada de
Filestore configura el mismo secreto y las dos variables CONTENT anteriores.
Roko y Filestore pasan estas variables explícitamente al contenedor; Bridge
conserva su env_file. El modo se decide en cada servicio y no por una cabecera
controlada por el usuario.

## Elegir contenido visible para pruebas

`OBSERVABILITY_CONTENT_MODE=redacted` es el valor predeterminado. Solo medidas,
estados y metadatos técnicos permitidos se exportan.

`OBSERVABILITY_CONTENT_MODE=debug` permite texto de mensajes, prompts y respuestas
con filtrado y truncado. Configúralo en Roko y Bridge para examinar ambos lados.
OBSERVABILITY_CONTENT_MAX_CHARS limita el texto total por input/output, por defecto
4000, mínimo 256 y máximo 16000. No promete un historial completo sin límites.

En ambos modos se excluyen secretos reconocidos, Authorization, cookies, campos
de entorno/comandos/rutas, bloques de archivos, binarios y Base64. Los textos de
código pueden verse en debug con secretos filtrados. Filestore permite contexto
de consultas/resultados acotados, pero no exporta campos content/text, cuerpos de
escritura ni el resultado documental de read_file. No es un anonimizador general:
el texto permitido en debug puede contener datos personales. Habilítalo solo para
las pruebas que quieres registrar. No modifica los datos que recibe el modelo.

Cada observación incluye metadata.contentMode para identificar su política.
Cambiar el modo afecta a nuevas exportaciones, no modifica trazas históricas.
No se puede recuperar después contenido que ya se exportó redactado.

## Proyectos Langfuse

Roko y Bridge deben exportar al mismo proyecto para ver un único árbol.
Para incorporar las herramientas MCP al mismo árbol, configura también en
Filestore LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY y LANGFUSE_SECRET_KEY del proyecto
Roko. Esto es opcional: conserva sus claves actuales si prefieres el proyecto
Filestore separado. En proyectos separados no se promete un árbol conjunto,
aunque los traceId y la firma permiten correlacionar los servicios. No hay doble
exportación automática. Ninguna clave Langfuse se propaga en las llamadas MCP.

## Aplicar desde el equipo local

Copia los archivos del ZIP correspondiente encima de TU repositorio actual,
sin borrar carpetas ni configuración privada. Consulta MODIFIED-FILES.json para
ver el delta exacto. En cada repositorio revisa `git diff` y `git status`; añade
solo los archivos de código/documentación indicados y haz commit/push a main.
El commit sugerido es `fix: unify interaction traces and add safe debug content`.

Roko, Bridge y Filestore se alojan en GitHub. Filestore se despliega con los
scripts /opt/test.sh y /opt/deploy.sh de su propia VM.

Tras el despliegue, si cambias únicamente el modo de contenido, recrea utilizando
la imagen fijada por el despliegue actual (un simple restart no cambia el entorno):

```bash
cd ~/services/roko
ROKO_DEPLOY_DIR="$HOME/services/roko" bash ci/compose.sh roko up -d --no-deps --no-build --pull never --force-recreate api
cd ~/services/bridge
BRIDGE_DEPLOY_DIR="$HOME/services/bridge" bash ci/compose.sh bridge up -d --no-deps --no-build --pull never --force-recreate bridge
```

En la VM Filestore, desde su directorio desplegado, para cambiar solo el entorno:

```bash
docker compose -f docker-compose.yml config -q
docker compose -f docker-compose.yml up -d --no-deps --no-build --force-recreate web
```

## Verificación en Langfuse

1. Deja los pipelines en verde y confirma la salud de los contenedores.
2. En debug envía una pregunta de prueba reconocible y verifica input/output en
   roko.interaction. El código Bridge instalado debe anunciar versión 0.7.2.
3. Comprueba que AgentRun y bridge.request comparten traceId, y que las herramientas
   cuelgan de sus padres. El padre de bridge.request puede ser la raíz activa;
   los callbacks LangChain no convierten cada span en contexto activo del modelo.
4. Envía otro mensaje: nueva traza y misma Session. Otro usuario debe obtener otra
   Session, incluso si se fuerza el mismo ID de conversación en una prueba.
5. Genera un .py/.docx/.pdf, busca en web y llama a una herramienta Filestore
   realmente disponible. Solo deben aparecer las operaciones realmente emitidas.
6. Cancela una ejecución: observa cancelled. Un error debe cerrar sus observaciones.
7. Comprueba que el archivo adjunto y las credenciales de prueba no aparecen en
   input/output. Vuelve a redacted y recrea para dejar el contenido protegido.

La exportación es asíncrona; espera a la ingestión. Las trazas antiguas no se
fusionan retroactivamente. La verificación contra tu Langfuse desplegado y los
proveedores reales debe hacerse en tu entorno; aquí se usan colectores OTLP locales.

## Rollback

En la VM rokoface, conserva `.ci-deploy/` y ejecuta el rollback existente:

```bash
cd ~/services/roko
ROKO_DEPLOY_DIR="$HOME/services/roko" bash ci/deploy.sh roko rollback
cd ~/services/bridge
BRIDGE_DEPLOY_DIR="$HOME/services/bridge" bash ci/deploy.sh bridge rollback
```

Para Filestore revierte el commit de esta actualización en tu equipo y haz push a
main; después prueba y despliega esa reversión con /opt/test.sh y /opt/deploy.sh. No borres volúmenes
ni bases de datos. Devuelve CONTENT_MODE a redacted y recrea si solo quieres apagar
la captura de contenido. No hay migraciones de datos en esta actualización.

## Fuentes técnicas

- https://langfuse.com/docs/observability/features/trace-ids-and-distributed-tracing
- https://langfuse.com/docs/observability/sdk/instrumentation
- Se inspeccionó el código instalado de @librechat/agents 3.2.35 y sus SDK exactos,
  no una migración de ejemplos de otra versión.
