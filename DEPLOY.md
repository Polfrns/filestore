# Actualización v0.7.2

Instrucciones actuales de esta entrega: [OBSERVABILITY-V072.md](OBSERVABILITY-V072.md). Incluye variables, despliegue, recreación por imagen fijada y rollback.

## Histórico v0.7.1

# Despliegue v0.7.1

Los archivos son repositorios completos de código, sin datos persistentes, logins ni .env. Mantén tus volúmenes y tu .env actuales. No ejecutes `down -v`. Antes de cambiar nada conserva una copia del código/configuración y etiqueta las imágenes actualmente instaladas como rollback. No se han aplicado cambios a tus servidores desde este entorno.

## 1. Variables

Bridge y Roko deben conservar el mismo BRIDGE_API_KEY / CLAUDE_BRIDGE_KEY que ya usas. Esta clave también fija la identidad de Sessions: si la rotas, las sesiones nuevas tendrán otra seudonimización. No cambies sus valores al copiar .env.example.

Añade la misma nueva OBSERVABILITY_TRACE_SECRET aleatoria (>=32 caracteres) a los .env de Roko, Bridge y Filestore. Puedes generarla localmente con `openssl rand -hex 32`; no la publiques ni la pongas en runtime-config.yaml. El secreto de trazado no sustituye ninguna API key ni permiso.

En Roko configura OBSERVABILITY_TRACE_TARGETS con las URL exactas de servicios autorizados. Ejemplo cuando Bridge comparte la red Docker cli y Filestore es una VM:

```dotenv
ROKO_BRIDGE_BASE_URL=http://cli-bridge:4000/v1
OBSERVABILITY_TRACE_TARGETS=http://cli-bridge:4000/v1,http://192.168.1.50:8000/mcp
OBSERVABILITY_FILE_NAMES=false
OTEL_TRACES_SAMPLER=always_on
LANGFUSE_FORCE_FLUSH_ON_DISPOSE=false
```

Sustituye la IP y el path MCP por los reales de tu configuración. Una URL autoriza su path y descendientes, con el mismo esquema/origen/puerto; no autoriza otros dominios. No uses comodines. El código mantiene las restricciones SSRF y de red existentes: esta variable no autoriza conexiones que el cliente MCP ya tenga prohibidas. Si Bridge sigue en host.docker.internal, usa esa misma URL en ambas variables. No publiques Bridge en 0.0.0.0 para resolver conectividad.

En los tres servicios conserva LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY y LANGFUSE_TRACING_ENABLED=true. Bridge/Filestore ya pasan el .env o las variables necesarias; Roko añade las nuevas variables al override incluido. Para verificar cada operación usa OTEL_TRACES_SAMPLER=always_on; muestrear menos es una elección operativa posterior.

Los proyectos siguen siendo independientes por sus credenciales: Roko+Bridge usan las del proyecto Roko y Filestore las del proyecto Filestore. Para obtener un árbol común configura deliberadamente las mismas credenciales de proyecto en los tres servicios. Los nombres de proyecto en la interfaz no son variables que haya que enviar. No existe un árbol navegable único entre dos proyectos distintos.

## 2. Bridge (en la carpeta del repo)

```bash
docker compose -f compose.yaml config -q
docker compose -f compose.yaml build bridge
docker compose -f compose.yaml up -d --no-deps bridge
docker compose -f compose.yaml ps
docker compose -f compose.yaml exec bridge node scripts/healthcheck.mjs
```

Mantén el nombre de proyecto y los volúmenes logins/runtime originales. No hay que repetir los logins de Codex/Claude. Docker instala las dependencias desde el lockfile actualizado. En ejecución local: `npm ci`, `npm test`; no copies node_modules ni runtime-tools de Windows a Linux.

## 3. Roko (en la carpeta de tu instalación)

Mantén docker-compose.yml, el override incluido y runtime-config.yaml con tus ajustes de entorno. El ZIP incluye la configuración base del adjunto; no reemplaces tus volúmenes, usuarios ni historial. El build requiere los archivos de todos los workspaces adjuntos, incluido packages/roko-server.

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml config -q
docker compose -f docker-compose.yml -f docker-compose.override.yml build api
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --no-deps api
docker compose -f docker-compose.yml -f docker-compose.override.yml ps
```

El Dockerfile ejecuta scripts/patch-observability.cjs después de copiar el código y antes de la compilación. No elimina el parche al podar dependencias. El Dockerfile.multi también lo instala en la imagen final. Para instalación fuera de Docker: `npm ci`, `npm run observability:patch`, `npm run frontend` y tu comando backend habitual. Repite el parche después de cada reinstalación de node_modules. El build se detiene si cambia agents 3.2.35; revisa esa nueva versión antes de desplegar, para evitar exportar datos sin protección.

Si no usas Bridge configura una OBSERVABILITY_SESSION_SECRET persistente de >=32 caracteres en Roko. Cuando existe CLAUDE_BRIDGE_KEY, se usa esta última para que coincida con el Bridge.

## 4. Leedeo-Filestore

El despliegue ya no se realiza mediante pipeline. Ejecuta en la VM de Filestore `/opt/test.sh` y `/opt/deploy.sh` (o tu procedimiento equivalente) tras el merge a main.

Añade las credenciales y OBSERVABILITY_TRACE_SECRET al .env del despliegue que ya utiliza /opt/deploy.sh. No se dispone del contenido de esos scripts externos y no se han sustituido ni ejecutado. Sube estos cambios al repositorio y haz que pasen por tu revisión/merge habitual a main. Antes del merge valida en la VM de despliegue:

```bash
docker compose -f docker-compose.yml config -q
docker compose -f docker-compose.test.yml config -q
```

Servicios de producción: db con image postgres:16 y web con build; tests: db-test con image y test con build/target:test. No se añade un servicio solo con environment. Mantén los directorios chroma_db, filestores_data, uploads y el volumen pgdata originales. Si necesitas comprobarlo tras el despliegue: `docker compose ps` y `docker compose logs --tail=50 web` desde el directorio de despliegue.

## 5. Verificación y rollback

Sigue VERIFY-LANGFUSE.md y TEST-RESULTS.md. Nunca imprimas el Compose expandido ni los .env en logs compartidos.

Para rollback, restaura el código y las imágenes etiquetadas antes del cambio, además de tu override anterior si lo cambiaste, y vuelve a ejecutar `up -d --no-deps` para bridge/api/web en su proyecto original. En Filestore revierte el commit mediante el flujo de revisión de main y vuelve a desplegar la versión previa. No hay migraciones de esquema de esta actualización. No restaures ni borres bases de datos o volúmenes para revertir observabilidad.

Desactivar solo exportación: LANGFUSE_TRACING_ENABLED=false y recrear los servicios. Las tarjetas e inferencia siguen disponibles en Bridge. Para desactivar tarjetas usa BRIDGE_ACTIVITY_ENABLED=false y/o ROKO_BRIDGE_ACTIVITY_ENABLED=false; las observaciones del Bridge siguen activas independientemente de la visibilidad de tarjetas.
