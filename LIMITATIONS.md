# Current limits and integration boundaries

- This repository implements the roko-workspace service. Roko's client-side async-context signing, global MCP toggle, Bridge model filtering, chat attachment tools, conversation deletion integration, and legacy sessions/filestores removal remain required separate changes in the Roko repository.
- Signed request contexts can be replayed for up to ten minutes for the same conversation. HMAC prevents changing tenant/user/conversation identity; a nonce store is intentionally not used.
- Shell inputs/ read-only enforcement uses a nested read-only bind mount sourced from an open directory descriptor. The startup canary checks it in the Linux container and keeps the service unavailable if the rule fails.
- Application quotas do not cap writes during a running shell command. Use a filesystem quota on the Docker data volume.
- Docker Compose provides an egress network so Langfuse can be reached; host firewall rules must restrict it to the approved Langfuse endpoint.
- Only Linux Docker is supported. Windows and non-containerized execution are out of scope.
- The seccomp filter supports Linux x86_64 and ARM64. The current integration environment exercises x86_64 only; ARM64 remains unverified.
- Output validation covers supported file formats and installed file-tools.py validators. It does not prove that a document's content is correct.
- The 30-minute TTL is based on last authenticated use. A workspace may expire while its conversation remains in Roko history.
