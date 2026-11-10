# Boundary Class Register & Probing Recipes

**Stage:** `STAGE_1_CONTRACT_PROBING`  
**Owner:** `stage-1`  
**Schema Governance:** `schemas/boundary-manifest.schema.json`, `schemas/contract-fixture.schema.json`

The Boundary Class Register covers boundaries with no process exit code (device, model, UI runtime, and the test harness itself) and grows through `OTHER`. In Stage 1, every boundary crossed by the feature must be inspected and recorded.

---

## The 10 Canonical Boundary Classes

### 1. `SUBPROCESS_CLI`
* **Scope**: Operating system process execution, shell commands, CLI utilities, child processes, container entrypoints.
* **What Must Be Recorded**:
  - Exit codes (success 0 vs distinct non-zero exit codes)
  - Stream delimiters (`\r` progress streams vs `\n` line delimiters)
  - ANSI/color escape suppression (`NO_COLOR=1`, `--no-color`, non-interactive tty)
  - Locale and encoding normalisation (`LC_ALL=C`, UTF-8 vs ASCII byte handling)
  - Argument-terminator handling (`--`) for user-supplied arguments
  - Signal propagation (`SIGTERM`, `SIGINT`, `SIGKILL`) and child orphan cleanup
  - Environment inheritance from user config and scratch-space expansion bounds
  - Non-fatal stream warnings emitted alongside a zero exit code
* **Canonical Properties**:
  1. `exit_codes`
  2. `stream_delimiters`
  3. `ansi_color_suppression`
  4. `locale_encoding_normalization`
  5. `argument_terminator_handling`
  6. `signal_propagation`
  7. `environment_inheritance`
  8. `scratch_space_expansion_bounds`
  9. `non_fatal_stream_warnings`
* **Example Instances**: FFmpeg transcoding pipelines, `git for-each-ref`, CLI packagers, system daemons.
* **Probing Recipe**:
  1. Run subprocess with `LC_ALL=C` and capture stdout/stderr separately.
  2. Test argument with `--` prefix to confirm argument parsing boundaries.
  3. Send `SIGTERM` mid-execution and assert scratch file cleanup.
  4. Induce non-fatal warning (e.g. deprecation flag) and assert stdout/stderr capture alongside exit code 0.
  5. Capture non-zero exit codes (e.g. exit 128 on lock contention, exit 1 on unmerged conflicts).

---

### 2. `NETWORK_API`
* **Scope**: REST, GraphQL, gRPC, WebSocket endpoints, internal microservices, SaaS APIs.
* **What Must Be Recorded**:
  - HTTP status codes and wire payload envelopes
  - All distinct failure envelope formats (JSON `detail`, HTML gateway errors, XML soap faults, transport drops)
  - Rate-limit headers (`Retry-After`, `x-ratelimit-reset`, `x-ratelimit-remaining`)
  - Auth expiry and token refreshment lifecycles
  - Credential rotation and dual-key rollover windows (rolling secret arrays, signature mismatch envelopes)
  - Authorization asymmetry across tenancy/ownership domains (origin vs fork, owner vs collaborator, service account vs user token)
  - Redirect and reverse-proxy interposition (Nginx 502/504 HTML responses)
* **Canonical Properties**:
  1. `status_codes`
  2. `error_envelope_shapes`
  3. `rate_limit_headers`
  4. `auth_expiry`
  5. `credential_rotation`
  6. `authorization_asymmetry`
  7. `redirect_proxy_interposition`
* **Example Instances**: Stripe billing webhooks/API, GitHub GraphQL API, internal RPC services.
* **Probing Recipe**:
  1. Issue happy-path requests across all variant selector cross-products (e.g. `is_summary × is_promoted`).
  2. Issue unauthenticated / expired token requests to record 401 error envelope.
  3. Trigger rate limiting to capture `Retry-After` header casing and format (seconds vs HTTP-date).
  4. Probe reverse proxy / gateway timeouts to capture raw HTML error responses.
  5. Test dual-key rotation by signing requests with secondary keys during key transition windows.

---

### 3. `FRAMEWORK_MIDDLEWARE`
* **Scope**: Server framework pipeline, body parsers, auth interceptors, route guards, stream framing.
* **What Must Be Recorded**:
  - Raw body buffer preservation requirements (unparsed binary buffers needed for HMAC signatures)
  - Parser execution ordering (body parser vs route guard vs rate limiter)
  - Stream framing and chunked transfer encoding boundaries
  - Signing-secret rotation windows and header casing normalisation
* **Canonical Properties**:
  1. `raw_body_buffer_requirements`
  2. `parser_ordering`
  3. `stream_framing`
  4. `signing_secret_rotation_windows`
* **Example Instances**: Express/Fastify raw body capture for Stripe webhook HMAC verification.
* **Probing Recipe**:
  1. Send valid webhook payload with HMAC signature and assert raw buffer integrity.
  2. Send malformed JSON body to verify whether parser throws 400 before or after route guard.
  3. Send chunked stream to test frame assembly before authentication middleware.

---

### 4. `DATASTORE`
* **Scope**: SQL databases, Key-Value stores, Document stores, local embedded DBs (SQLite), filesystem indices.
* **What Must Be Recorded**:
  - Transaction isolation and timeout semantics
  - Constraint and conflict error codes (unique constraint, foreign key, deadlock)
  - Lock behavior and contention (`SELECT ... FOR UPDATE`, `.git/index.lock`, table locks)
  - Connection pool capacity and timeout under concurrent burst
  - Multi-root and linked-instance topologies (linked Git worktrees, attached SQLite databases)
* **Canonical Properties**:
  1. `transaction_timeout_semantics`
  2. `constraint_conflict_error_codes`
  3. `lock_behavior_contention`
  4. `connection_pool_capacity`
  5. `multi_root_linked_instances`
* **Example Instances**: PostgreSQL connection pools, SQLite WAL mode, Git index locks across worktrees.
* **Probing Recipe**:
  1. Execute concurrent competing transactions to trigger lock contention error code.
  2. Insert duplicate primary keys to record exact SQL state error code (`23505` vs driver exception).
  3. Exceed connection pool limit and record client-side pool exhaustion timeout error shape.

---

### 5. `MESSAGE_TRANSPORT`
* **Scope**: Task queues (BullMQ, Celery), message brokers (RabbitMQ, Kafka), pub/sub (MQTT, Redis Streams), webhooks.
* **What Must Be Recorded**:
  - Delivery guarantees (at-least-once vs at-most-once)
  - Redelivery and duplicate message delivery behavior
  - Ordering guarantees (FIFO partitions vs unordered concurrent consumers)
  - Offline buffering and backpressure handling
  - QoS and acknowledgment semantics (`acks_late=True`, manual ack vs auto-ack)
  - Visibility timeout and distributed lease duration vs expected task execution duration
* **Canonical Properties**:
  1. `delivery_guarantee`
  2. `redelivery_duplication`
  3. `ordering_guarantee`
  4. `offline_buffering`
  5. `qos_ack_semantics`
  6. `visibility_timeout_lease_duration`
* **Example Instances**: BullMQ job processing, RabbitMQ dead-letter queues, MQTT telemetry streams.
* **Probing Recipe**:
  1. Enqueue identical tasks with same idempotency key and verify deduplication interval.
  2. Delay worker acknowledgment beyond visibility timeout to capture redelivery behavior.
  3. Disconnect consumer and assert broker buffer capacity and drop/backpressure policy.

---

### 6. `DEVICE_IO`
* **Scope**: Hardware sensors, serial ports, BLE peripherals, camera feeds, GPIO, system clocks.
* **What Must Be Recorded**:
  - Units and sample rates
  - Stale, absent, or out-of-range readings
  - Reconnect and backoff behavior on physical disconnect
  - Clock drift between device and host system
  - Power-state transitions (sleep, low-power mode, wake-up latency)
* **Canonical Properties**:
  1. `units_sample_rate`
  2. `stale_absent_out_of_range_readings`
  3. `reconnect_behavior`
  4. `host_clock_drift`
  5. `power_state_transitions`
* **Example Instances**: Temperature sensor over I2C, BLE heart rate monitor, serial GPS receiver.
* **Probing Recipe**:
  1. Probe device under normal operating conditions to capture telemetry payload and units.
  2. Simulate sensor disconnection to record error event and reconnect loop timing.
  3. Inject out-of-range value (e.g. NaN, -999) to record hardware fault envelope.

---

### 7. `MODEL_ENDPOINT`
* **Scope**: LLM completion APIs, tool-calling endpoints, embedding models, local ONNX/GGUF runtimes.
* **What Must Be Recorded**:
  - Context and token output limits
  - Truncation behavior on exceeding maximum tokens
  - Refusal and safety filter responses
  - Malformed structured output envelopes (invalid JSON, schema mismatch)
  - Tool-call schema drift and parameter hallucinations
  - Non-determinism across identical prompt invocations
* **Canonical Properties**:
  1. `context_output_limits`
  2. `truncation_behavior`
  3. `refusal_safety_responses`
  4. `malformed_structured_output`
  5. `tool_call_schema_drift`
  6. `input_non_determinism`
* **Example Instances**: Claude/Gemini tool calling, OpenAI structured output endpoints.
* **Probing Recipe**:
  1. Run tool-call request with structured schema and capture response envelope.
  2. Send prompt exceeding token limit to record specific truncation or 400 error shape.
  3. Trigger safety refusal prompt to record structured refusal payload.

---

### 8. `UI_RUNTIME`
* **Scope**: Browser DOM, React/Vue lifecycle, mobile view hierarchy, window managers, web workers.
* **What Must Be Recorded**:
  - Render and teardown lifecycle
  - Development-mode double-invocation semantics (React 19 StrictMode effect remounting)
  - Overlay and portal mounting (DOM container attachment and z-index ordering)
  - Platform permission dialogs (notifications, geolocation, camera)
  - Background, foreground, and low-memory eviction lifecycles
* **Canonical Properties**:
  1. `render_and_teardown_lifecycle`
  2. `development_mode_double_invocation`
  3. `overlay_portal_mounting`
  4. `platform_permission_dialogs`
  5. `background_low_memory_eviction`
* **Example Instances**: React 19 StrictMode components, browser portal dialogs, service worker lifecycles.
* **Probing Recipe**:
  1. Mount component in StrictMode and record whether effects execute twice.
  2. Render modal dialog and assert portal attachment to `document.body`.
  3. Trigger unmount during async fetch and assert cleanup to prevent memory leaks.

---

### 9. `VERIFICATION_HARNESS`
* **Scope**: Test doubles, mock frameworks (Moto, MSW), in-memory DBs (SQLite `:memory:`), fake timers.
* **What Must Be Recorded**:
  - Fake clock and flush/settle semantics (`advanceTimers`, microtask queue draining)
  - Query scope (in-tree rendered nodes vs document-level globals)
  - Differential parity against the live boundary's failure envelopes (`HARNESS_FIDELITY_DRIFT`)
* **Canonical Properties**:
  1. `fake_clock_flush_semantics`
  2. `query_scope`
  3. `differential_parity_against_live`
* **Example Instances**: Moto S3 double, SQLite in-memory double, Jest/Vitest fake timers.
* **Probing Recipe**:
  1. Run mock against the exact failure envelopes recorded for the real boundary.
  2. Assert that double returns matching HTTP status code and error class (flag `HARNESS_FIDELITY_DRIFT` if mock returns 200/204 where live boundary returns 404).
  3. Assert fake timer progression accurately drains async microtask queues.

---

### 10. `OTHER`
* **Scope**: Boundaries not fitting the 9 standard classes (e.g. custom bundler plugins, dev-vs-prod asset serving).
* **What Must Be Recorded**:
  - Written boundary description
  - Register amendment proposal
  - Success samples, failure envelopes, and partitioned variance censuses
* **Canonical Properties**:
  - Custom properties defined in the manifest row.
* **Probing Recipe**:
  1. Document the boundary rationale and proposed amendment in `manifest.json`.
  2. Record success samples and reachable failure envelopes according to the tripartite recording rule.

---

## Known fragilities

* **An `UNBOUNDED` region is declared, not detected.** `unbounded_regions` lives inside `variance_census`, so removing the census removes the obligation and a planner who declares no open region owes no census. Where the recorded contract carries `additionalProperties: true`, an `any`, or an untagged union, unbounded-ness is derivable — a later change should derive it.
* **The three-sample total can be padded by a duplicate.** Appending a copy of an existing sample satisfies the total while adding no information. The per-cell floor is the real defence, and it only bites once the cell set is derived rather than declared — filed as `agent-skills-afp-partition-cell-derivation-ldd`.

