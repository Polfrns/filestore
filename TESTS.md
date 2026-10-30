# Test catalogue

## Unit tests (npm test)

test/unit/mcp-server.test.mjs verifies that MCP registers exactly the nine approved tools and does not expose get_output.

test/unit/workspace-service.test.mjs verifies that file tools reject writes to Roko-owned inputs/ before workspace creation and reject traversal before filesystem access.

test/unit/http-server.test.mjs verifies public health reports failed readiness, invalid identity is rejected before parsing an MCP body, and explicit attachment uploads are stored without overwriting earlier files.

## Sandbox integration tests

npm run test:sandbox runs bubblewrap in Docker with temporary workspaces. It verifies writable work/outputs/, readable but non-writable inputs/, sibling-data read/write denial, blocked nested user namespaces, network isolation against the service interface and an external TCP target, command timeout, and file-tools.py validation inside the sandbox. These checks require Docker-compatible Linux permissions and are not run on Windows. Run the container with the same security options as compose.yaml, including systempaths=unconfined; without it bubblewrap cannot mount the sandbox procfs and both tests fail with "Can't mount proc".

## Container verification

npm run test:docker builds the test image target and runs the Node unit suite in a read-only container with writable temporary /data. npm run check:workspace runs the actual bubblewrap canary and Python document-tool check on Linux.
