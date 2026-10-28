# Actualización v0.7.2

Verificación actual: [OBSERVABILITY-V072.md](OBSERVABILITY-V072.md), sección Verificación en Langfuse.

## Histórico v0.7.1

# Verificación en Langfuse

1. Crea una conversación nueva en Roko. Pide generar hola_mundo.py y después modificarlo en el siguiente mensaje. Comprueba que recibes los archivos y los cambios acumulados de la forma habitual.
2. En el proyecto Roko filtra por `bridge.request`. Debes ver una traza distinta por petición y `model.codex` o `model.claude` como generación, con operaciones reales debajo. La traza del agente Roko tendrá el mismo traceId cuando la propagación esté configurada y el mismo session.id seudonimizado. Cada mensaje conserva el conversationId de LibreChat; este nunca se sustituye por un ID de ejecución.
3. Abre Sessions y agrupa los dos mensajes. Ambos deben compartir el hash de sesión. Una conversación distinta o un usuario distinto debe producir otro hash. El responseMessageId/runId de Roko sigue siendo distinto por petición y sirve para su feedback habitual.
4. En el chat las tarjetas deben aparecer antes del texto final, actualizar estado y mostrar duración disponible. Los reintentos conocidos llevan intento 2; canceladas y fallidas tienen estado propio. Puedes activar OBSERVABILITY_FILE_NAMES=true en Bridge para mostrar basenames permitidos; no muestra rutas internas.
5. Solicita una búsqueda con Codex y un agente autorizado para usar una herramienta de Filestore. Espera `web.search` solo si el SDK la emite y `mcp.<nombre_real>` solo si se ejecuta la herramienta. Lee un documento privado con un usuario sin permiso: el permiso debe seguir rechazándolo y la observación debe registrar fallo, sin texto del documento.
6. Con proyectos separados consulta el mismo traceId/session.id en ambos proyectos. Filestore conserva el parentSpanId remoto pero ese padre puede no existir en su proyecto. Para un árbol común usa el mismo proyecto en los tres servicios; no se fusionan proyectos desde el código.
7. Inspecciona input/output y atributos exportados con un DOCX, un PDF y un archivo de código de prueba que contengan una cadena de control. No deben aparecer sus contenidos ni Base64 ni Authorization. Se conservan tipo de operación, extensión, medidas y estado permitidos. Las trazas antiguas NO se borran: revisa su retención/eliminación desde Langfuse según tus necesidades.
8. Cancela una respuesta y prueba un timeout controlado. En el servidor se cierran spans y operaciones pendientes. Un navegador ya desconectado no puede recibir el último estado; la tarjeta original de Roko presenta como cancelada una operación incompleta cuando deja de enviarse.
9. Simula Langfuse no disponible con credenciales de prueba/URL de prueba y verifica que la respuesta y los archivos siguen llegando una sola vez. No hagas esta prueba sustituyendo las claves reales por valores que luego olvides restaurar.

Las pruebas automáticas incluyen receptores OTLP locales y el SDK real, pero no se ha accedido a tu instancia Langfuse ni a las cuentas reales de Codex/Claude. Esta comprobación posterior al despliegue sigue siendo necesaria.
