# Security

This document describes the threat model, the security measures that are
**actually implemented** (with the file that implements them), and the gaps that
remain. It ends with an explicit "this is not a production SaaS" section.

## 1. Threat model

AI Workbench is a local-first application. The realistic threats considered are:

| # | Threat | Relevant surface |
|---|---|---|
| T1 | Malicious or malformed file upload (path traversal, oversized file, wrong type, Windows reserved names) | `POST /documents/upload` |
| T2 | Reading another user's data | every authenticated endpoint |
| T3 | Credential theft / weak password storage | `POST /auth/login`, `POST /auth/register` |
| T4 | Token forgery or replay | bearer-token authentication |
| T5 | SQL injection | all queries |
| T6 | Command / code injection through model output or tool input | the agent, the `calculator` tool |
| T7 | Secret leakage into logs | logging |
| T8 | Denial of service via a huge or slow job | upload size, worker timeout |
| T9 | Prompt injection causing the model to fabricate or leak | RAG and agent prompts |
| T10 | Cross-origin abuse from a browser | CORS |

Out of scope: a hostile network operator, a compromised host, supply-chain attacks,
and multi-tenant isolation at the infrastructure level.

## 2. Implemented measures

### 2.1 File upload safety (`app/infrastructure/files.py`)

* **Extension allow-list.** `ALLOWED_EXTENSIONS` defaults to
  `.md,.markdown,.txt,.pdf`; anything else raises `UnsupportedMediaTypeError`
  (415).
* **MIME consistency check.** `validate_content_type` rejects a declared
  `Content-Type` that is inconsistent with the extension (e.g. `.pdf` with
  `text/html`). `application/octet-stream` and a missing type are accepted.
* **Streaming size limit.** The file is read in 256 KB chunks and the running
  total is compared against `MAX_UPLOAD_MB` *while streaming*; on breach the
  partial file is deleted and `PayloadTooLargeError` (413) is raised. A rejected
  upload never leaves a partial file behind (asserted by a unit test).
* **Generated stored filenames.** The stored name is `uuid4().hex + suffix`,
  never derived from user input, so path traversal, NUL bytes and Windows
  reserved names are structurally impossible. `sanitize_filename` additionally
  strips directories, control characters and reserved names for the *display*
  name.
* **Storage-root containment.** `ensure_within_root` resolves a path and refuses
  to operate on anything outside `STORAGE_DIR`; `delete_file` uses it and refuses
  to delete outside the root.
* **Empty-file rejection.** A zero-byte upload raises `StorageError`.

### 2.2 Authentication and password storage (`app/core/security.py`)

* **bcrypt** password hashing with cost **12**; long passwords are truncated to
  bcrypt's 72-byte limit rather than crashing.
* **JWT (HS256)** access tokens signed with `SECRET_KEY`, carrying `sub`, `iat`,
  `exp`, `typ` and a `jti`. Expiry defaults to `ACCESS_TOKEN_EXPIRE_MINUTES`
  (1440 = 24 h). Invalid and expired tokens raise typed `AuthenticationError`s
  (`token_invalid`, `token_expired`).
* **Timing-safe login.** When the identifier does not exist, the service still
  performs a bcrypt comparison against a dummy hash, so response timing does not
  reveal whether an account exists.
* **Constant-time comparison** helper (`hmac.compare_digest`) is provided for
  secret comparisons.

### 2.3 Authorisation / tenancy (`app/api/deps.py`, repositories)

* Every data endpoint depends on `get_current_user`, which validates the token,
  loads the `User`, and rejects a missing, deleted or disabled account.
* Repositories expose `get_owned(id, owner_id)` and services call them, so a
  request can only reach rows belonging to the caller. Search and chat validate
  that each requested knowledge-base id is owned before use.
* Health endpoints are intentionally unauthenticated so orchestrators can probe
  them; they expose no user data.

### 2.4 Input validation (`app/schemas/`, Pydantic v2)

* Every request body and query parameter is validated by Pydantic; a failure
  becomes a `validation_error` (422) with per-field details.
* Enumerations (status, priority, search mode, run type, actions) are validated;
  an invalid value is a 422.
* Business rules surface as typed errors: 409 for duplicate names, completing a
  task with unfinished dependencies, illegal status transitions and dependency
  cycles.

### 2.5 SQL injection resistance

* All database access goes through SQLAlchemy with **parameterised** statements;
  no query is built by string concatenation of user input.
* User input used in `ILIKE` patterns is escaped (`%`, `_`, `\`) in
  `apply_search` and `_escape_like` before being embedded.

### 2.6 Command / code injection resistance

* The `calculator` tool uses `safe_eval`, an AST-restricted evaluator that allows
  only arithmetic operators and rejects everything else. Python `eval` is never
  used on user or model input.
* The agent cannot execute arbitrary code or reach the database; its only write
  path is the typed tool registry, and each write operation is capped per run.

### 2.7 Secret handling and log hygiene (`app/core/logging.py`)

* `mask_secret` renders a secret as `ab12...yz89 (len=N)`; it is never logged raw.
* The log formatter sanitises `extra` keys whose name contains `api_key`,
  `apikey`, `authorization`, `password`, `secret` or `token`, replacing the value
  with `<redacted>`, and truncates any string longer than 500 characters.
* `_redact` removes the password from the database URL in logs.
* A central guard (`_safe_make_record`) drops reserved keys passed through
  `extra=`, preventing `KeyError: Attempt to overwrite 'message' in LogRecord`.
  This guard exists because a careless `extra={"message": ...}` in an exception
  handler once turned a handled 409 into a 500; see
  [../docs/thesis/09-系统测试.md](thesis/09-系统测试.md).
* Secrets live in `.env`, which must not be committed; `.env.example` is the
  committed template and contains no real values.

### 2.8 CORS

`CORSMiddleware` is configured with an explicit origin allow-list
(`CORS_ORIGINS`), credentials enabled, and `X-Request-ID` / `X-Process-Time-Ms`
exposed. The default allow-list is the local dev origins only.

### 2.9 Availability / DoS controls

* Upload size cap (streaming) and a hard `MAX_PDF_PAGES = 500` limit.
* Worker jobs have a bounded attempt count and a job-level
  `asyncio.timeout(WORKER_JOB_TIMEOUT_SECONDS)`.
* A database-backed queue with `FOR UPDATE SKIP LOCKED` prevents duplicate
  processing without an external broker.
* Cache and vector-store outages degrade rather than fail the request.

### 2.10 Prompt-level guardrails

* The `rag_answer` prompt instructs the model to use only the supplied fragments,
  to cite every conclusion, and to say when the material is insufficient. The
  pipeline additionally refuses to mark an answer as `grounded` when no valid
  citation markers are present.
* The `agent_system` prompt forbids database access and requires retrieving the
  knowledge base before producing content.
* These are prompt-level mitigations, not a hard security boundary; see the gaps
  below.

### 2.11 Error handling

* Unexpected errors are logged with a stack trace and returned as
  `internal_error`; when `DEBUG=false` the exception detail is not leaked to the
  client. The same applies to `database_error`.
* No silent `except: pass` is used; every caught exception is logged or re-raised.

## 3. This is not a production SaaS

The project is a local-first developer/graduate-project deployment. The
following gaps are real and are not implemented:

| Gap | Detail |
|---|---|
| **No rate limiting** | A `RateLimitedError` code exists, but no middleware enforces limits. Login, chat, agent and upload endpoints can be hammered. |
| **No refresh-token rotation** | Only long-lived access tokens (24 h default) exist; there is no refresh token, no revocation list and no server-side session invalidation. Logout is client-side only. |
| **No audit log** | There is an activity feed and an AI-run trace, but no tamper-evident security audit log of authentication and authorisation events. |
| **Single-tenant assumptions** | Data is scoped by `owner_id`, but there is no organisation/team model, no roles beyond `is_superuser`, and no admin surface. |
| **No antivirus / content scanning on uploads** | Files are validated by extension and MIME only; the content is never scanned. |
| **No HTTPS in the dev setup** | The API binds to `127.0.0.1` over plain HTTP. TLS termination is not provided. |
| **Prompt injection not fully mitigated** | Retrieved content and tool results are untrusted text fed to the model. Guardrails are prompt-level; a determined injection is not prevented. |
| **Secrets in `.env` only** | No secrets manager, no key rotation, no encryption at rest for the `.env` file. |
| **Redis has no auth** | The generated `redis.conf` binds to 127.0.0.1 with `protected-mode yes` but no password; persistence is disabled, so the cache is disposable. |
| **No CSRF protection** | Not needed for a bearer-token API used by a non-browser client, but there is no CSRF token if a cookie-based session is ever added. |
| **Dependency scanning absent** | No automated vulnerability scanning of dependencies or the container/image. |
| **No PII classification** | Uploaded documents may contain personal data; there is no classification, retention policy or deletion workflow beyond manual document deletion. |

## 4. Related documents

* [deployment.md](deployment.md) — production hardening notes
* [architecture.md](architecture.md) — the request lifecycle and error handling
* [../docs/thesis/03-可行性分析.md](thesis/03-可行性分析.md) — security feasibility in Chinese
