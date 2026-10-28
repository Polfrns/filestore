# v0.7.2 — 2026-10-16

Modo redacted/debug en exportador OTLP, con contexto acotado de herramientas y documentos de read_file siempre excluidos. Contratos y permisos MCP sin cambios. Variables de modo propagadas por Compose. Scripts externos de despliegue intactos.

Entrega basada únicamente en los ZIP actuales del 16/10. Véase OBSERVABILITY-V072.md.

## Histórico anterior

# Observabilidad v0.7.1 — 2026-10-15

## Bridge
- Base exclusiva del adjunto bridge(1).zip; versión previa 0.7.0.
- Clasificación por datos reales de Codex SDK 0.161.0 y Claude Agent SDK 0.3.291: intérpretes directos, cambios de archivo, web, MCP y herramientas de lectura/escritura.
- Nombres específicos en spans y marcadores V1 ampliados con duración, cancelación, intento, extensión, estado, salida numérica y categorías de error disponibles.
- Una observación por llamada/intento; terminales duplicados y regresiones ignorados. Cierre de actividad pendiente también con tracing deshabilitado.
- Instrumentación de preparación de adjuntos, listado del workspace, recogida y validación reales; eventos de archivo generado solo después de validación. Nada se deriva de la petición textual.
- Streaming de actividad por Chat Completions y Responses, antes de terminar inferencia. Nunca se transforma actividad interna en tool_calls ejecutables.
- Sesiones estables con HMAC, nuevas trazas por petición, contexto remoto firmado y acotado. Mantiene las claves de aislamiento originales.
- Exportación con proyección privada previa al procesador Langfuse y a media uploads. Dependencias de observabilidad fijadas en package-lock, incluyendo instalación local.

## Roko
- Base exclusiva Roko(3).zip; LibreChat v0.8.6, @librechat/api 1.7.31, @librechat/agents 3.2.35, Langfuse 5.3.0.
- Consumidor V1 retrocompatible: nombres traducidos, duración, reintentos, cancelación y basenames opcionales. Mantiene el componente ToolCall de las tarjetas originales; son solo presentación.
- Sesiones seudonimizadas en el exportador real de agents, conservando conversationId y thread_id de LibreChat.
- Propagación dinámica por petición hacia Bridge y MCP, con HMAC y lista explícita de destinos. No almacena contexto de trazado en conexiones MCP compartidas.
- Protección del exportador nativo LangChain/Langfuse CJS y ESM, y del exportador OTLP general. Modelos y documentos reales permanecen intactos.
- Parche de instalación idempotente, verificado contra agents 3.2.35; fallo de build si cambia esa versión. Dockerfile y Dockerfile.multi aplican el parche.
- Lockfile corregido para el workspace adjunto packages/roko-server; no se sustituye por una versión antigua. No modifica la versión funcional de LibreChat.

## Filestore
- Base exclusiva server(2).zip; Django 6.1.1, MCP 2.2.0, OpenTelemetry 1.45.0.
- Mantiene las 12 funciones MCP y sus firmas, autenticación y reglas de acceso. Spans mcp.<nombre_real>, estado, duración, categoría de error y extensión segura.
- Contexto W3C aceptado únicamente tras autenticar y verificar HMAC de servicio vigente. ContextVars restauradas al terminar.
- El exportador OTLP elimina atributos no autorizados, eventos y texto de errores. No exporta argumentos ni resultados documentales.
- Cierre ante cancelaciones y excepciones, tracing fail-open sin repetir herramientas.
- Compose conserva image/build en todos los servicios.
