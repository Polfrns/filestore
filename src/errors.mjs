/**
 * Stable error codes shared by MCP results and REST responses.
 * @typedef {'unauthorized'|'invalid_path'|'not_found'|'read_only_zone'|'not_text'|'no_match'|'ambiguous_match'
 *   |'too_large'|'quota_exceeded'|'limit_exceeded'|'busy'|'timeout'|'sandbox_unavailable'|'file_tool_failed'
 *   |'unsupported_conversion'|'invalid_output'|'invalid_request'} ErrorCode
 */

/** @type {Readonly<Record<ErrorCode, number>>} */
export const HTTP_STATUS = Object.freeze({
  unauthorized: 401,
  invalid_path: 400,
  invalid_request: 400,
  not_found: 404,
  read_only_zone: 403,
  not_text: 422,
  no_match: 422,
  ambiguous_match: 422,
  too_large: 413,
  quota_exceeded: 507,
  limit_exceeded: 429,
  busy: 409,
  timeout: 504,
  sandbox_unavailable: 503,
  file_tool_failed: 422,
  unsupported_conversion: 422,
  invalid_output: 422,
});

export class WorkspaceError extends Error {
  /** @param {ErrorCode} code @param {string} message */
  constructor(code, message) {
    super(message);
    this.name = 'WorkspaceError';
    this.code = code;
  }

  get status() { return HTTP_STATUS[this.code]; }
}
