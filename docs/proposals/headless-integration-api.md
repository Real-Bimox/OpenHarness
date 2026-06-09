# Proposal: Headless Integration API

**Status:** DRAFT
**Author:** OpenHarness Team
**Date:** 2026-06-09
**Priority:** Critical

---

## Executive Summary

This proposal defines the API surface required to operate OpenHarness in a fully headless manner, enabling integration with in-house solutions without any manual interaction within OpenHarness itself.

**Core Principle:** Every operation that currently requires TUI interaction must be exposed via programmatic APIs (HTTP, WebSocket, CLI, or Python SDK).

---

## 1. Problem Statement

Currently, OpenHarness requires manual interaction for:
- Permission approvals (prompts user for confirmation)
- Session management (resume/continue via CLI flags only)
- Memory management (slash commands only)
- Configuration changes (edit settings.json manually)
- Plugin/hook management (CLI subcommands, no runtime control)
- Task monitoring (CLI commands, no real-time updates)
- Error recovery (manual intervention required)
- Channel management (config file only)

**Impact:** Cannot integrate OpenHarness into automated workflows, CI/CD pipelines, or external orchestration systems.

---

## 2. Required API Surface

### 2.1 Core APIs (Critical Path)

#### 2.1.1 HTTP/REST API Server
**Purpose:** Primary interface for external systems to control OpenHarness.

**Endpoints:**

```
POST /api/v1/query
  - Submit a query/prompt
  - Parameters: { prompt, session_id?, model?, permission_mode?, tools?, timeout? }
  - Returns: { query_id, status, stream_url? }

GET /api/v1/query/{query_id}/stream
  - SSE stream of query events
  - Events: text_delta, tool_start, tool_result, complete, error

POST /api/v1/session
  - Create new session
  - Parameters: { cwd?, model?, system_prompt?, permission_mode? }
  - Returns: { session_id, created_at }

GET /api/v1/session/{session_id}
  - Get session state
  - Returns: { session_id, status, messages_count, created_at, last_active }

POST /api/v1/session/{session_id}/resume
  - Resume existing session
  - Returns: { session_id, status }

DELETE /api/v1/session/{session_id}
  - Terminate session
  - Returns: { success }

GET /api/v1/sessions
  - List all sessions
  - Parameters: { status?, limit?, offset? }
  - Returns: { sessions: [...], total }

POST /api/v1/permission/approve
  - Pre-approve permissions
  - Parameters: { tool_name?, path_pattern?, command_pattern?, scope: "session"|"global" }
  - Returns: { rule_id }

GET /api/v1/permissions
  - List active permission rules
  - Returns: { rules: [...] }

DELETE /api/v1/permission/{rule_id}
  - Remove permission rule

POST /api/v1/memory
  - Add memory entry
  - Parameters: { content, type?, scope?, tags? }
  - Returns: { memory_id, path }

GET /api/v1/memory
  - Search memories
  - Parameters: { query?, type?, scope?, limit? }
  - Returns: { memories: [...] }

DELETE /api/v1/memory/{memory_id}
  - Remove memory entry

GET /api/v1/memory/entrypoint
  - Get memory entrypoint document
  - Returns: { content, last_updated }

GET /api/v1/config
  - Get current configuration
  - Returns: { settings: {...} }

PUT /api/v1/config
  - Update configuration
  - Parameters: { settings: {...} }
  - Returns: { success, applied: [...] }

GET /api/v1/health
  - Health check
  - Returns: { status: "ok"|"degraded"|"error", version, uptime }

GET /api/v1/metrics
  - Usage metrics
  - Returns: { queries_total, tokens_used, cost_estimate, active_sessions }
```

**Implementation:**
- Use FastAPI or Starlette for async HTTP server
- Run as background task when `--api-server` flag is used
- Bind to configurable host:port (default: `127.0.0.1:8080`)
- API key authentication via `Authorization: Bearer <key>` header
- CORS support for browser-based clients

**Priority:** P0 (Critical)
**Effort:** 3-4 weeks
**Dependencies:** None

---

#### 2.1.2 WebSocket API
**Purpose:** Real-time bidirectional communication for interactive headless clients.

**Endpoints:**

```
WS /api/v1/ws
  - Bidirectional WebSocket connection
  - Client messages:
    - { type: "query", prompt: "...", session_id?: "..." }
    - { type: "permission_response", query_id: "...", approved: true/false }
    - { type: "cancel", query_id: "..." }
    - { type: "session_resume", session_id: "..." }

  - Server messages:
    - { type: "text_delta", query_id: "...", text: "..." }
    - { type: "tool_start", query_id: "...", tool: "...", input: {...} }
    - { type: "tool_result", query_id: "...", tool: "...", output: "..." }
    - { type: "permission_request", query_id: "...", tool: "...", path: "..." }
    - { type: "complete", query_id: "...", usage: {...} }
    - { type: "error", query_id: "...", message: "..." }
    - { type: "session_state", session_id: "...", state: {...} }
```

**Implementation:**
- Use `websockets` library (already in dependencies)
- Integrate with existing `QueryEngine.submit_message()` stream
- Auto-approve permissions if `permission_mode=full_auto` or pre-approved
- Support multiple concurrent sessions per connection

**Priority:** P0 (Critical)
**Effort:** 2-3 weeks
**Dependencies:** HTTP API server (shared infrastructure)

---

#### 2.1.3 Python SDK
**Purpose:** Native Python interface for programmatic embedding.

**API:**

```python
from openharness.sdk import OpenHarnessClient

# Initialize client
client = OpenHarnessClient(
    api_url="http://localhost:8080",
    api_key="your-api-key",
    cwd="/path/to/project"
)

# Create session
session = client.create_session(
    model="claude-sonnet-4-6",
    permission_mode="full_auto",
    system_prompt="You are a helpful assistant."
)

# Submit query (blocking)
result = session.query("List all Python files in this directory")
print(result.text)
print(result.tool_calls)

# Submit query (streaming)
for event in session.query_stream("Explain this codebase"):
    if event.type == "text_delta":
        print(event.text, end="")
    elif event.type == "tool_start":
        print(f"[Running {event.tool}...]")

# Memory operations
session.memory.add("This project uses FastAPI", type="decision")
memories = session.memory.search("API framework")

# Session management
sessions = client.list_sessions()
session = client.resume_session(session_id="abc123")
session.terminate()

# Configuration
client.config.set("model", "gpt-4o")
client.config.set("permission.mode", "full_auto")
```

**Implementation:**
- Thin wrapper around HTTP API using `httpx`
- Provide both sync and async interfaces
- Type hints for all methods
- Automatic retry and error handling

**Priority:** P0 (Critical)
**Effort:** 1-2 weeks
**Dependencies:** HTTP API server

---

#### 2.1.4 MCP Server Mode
**Purpose:** Expose OpenHarness tools to other MCP clients (IDEs, other agents).

**Capabilities:**

```json
{
  "name": "openharness",
  "version": "0.1.9",
  "tools": [
    {
      "name": "openharness_query",
      "description": "Submit a query to OpenHarness agent",
      "inputSchema": {
        "type": "object",
        "properties": {
          "prompt": { "type": "string" },
          "session_id": { "type": "string" },
          "model": { "type": "string" }
        },
        "required": ["prompt"]
      }
    },
    {
      "name": "openharness_memory_add",
      "description": "Add a memory entry",
      "inputSchema": { ... }
    },
    {
      "name": "openharness_memory_search",
      "description": "Search memories",
      "inputSchema": { ... }
    },
    ... (all 42 built-in tools exposed)
  ],
  "resources": [
    {
      "uri": "openharness://session/{session_id}/messages",
      "name": "Session Messages",
      "description": "Conversation history"
    },
    {
      "uri": "openharness://memory/entrypoint",
      "name": "Memory Entrypoint",
      "description": "Project memory document"
    }
  ]
}
```

**Implementation:**
- Implement `McpServer` class using `mcp` library (already in dependencies)
- Wrap existing tools with MCP tool adapters
- Support stdio and HTTP/SSE transports
- Run as standalone server: `openharness mcp-server --transport stdio`

**Priority:** P0 (Critical)
**Effort:** 2-3 weeks
**Dependencies:** Tool registry (already exists)

---

### 2.2 Automation APIs (High Priority)

#### 2.2.1 Webhook/Event System
**Purpose:** Notify external systems of OpenHarness events.

**Configuration:**

```json
{
  "webhooks": [
    {
      "url": "https://your-system.com/webhook",
      "events": ["session.created", "query.completed", "error.occurred"],
      "secret": "hmac-secret",
      "headers": { "Authorization": "Bearer token" }
    }
  ]
}
```

**Events:**

```json
{
  "event": "query.completed",
  "timestamp": "2026-06-09T18:00:00Z",
  "session_id": "abc123",
  "query_id": "def456",
  "data": {
    "prompt": "...",
    "response": "...",
    "tool_calls": [...],
    "usage": { "input_tokens": 1000, "output_tokens": 500 }
  },
  "signature": "sha256=..."
}
```

**Event Types:**
- `session.created`, `session.resumed`, `session.terminated`
- `query.started`, `query.completed`, `query.failed`
- `tool.executed`, `tool.failed`
- `permission.requested`, `permission.granted`, `permission.denied`
- `memory.added`, `memory.removed`
- `error.occurred`

**Implementation:**
- Async HTTP POST with retry (3 attempts, exponential backoff)
- HMAC-SHA256 signature in `X-Webhook-Signature` header
- Configurable via `settings.json` or API
- Queue events if webhook is unreachable (in-memory queue, max 1000)

**Priority:** P1 (High)
**Effort:** 1-2 weeks
**Dependencies:** HTTP client (already exists)

---

#### 2.2.2 External Permission Provider
**Purpose:** Delegate permission decisions to external system.

**API:**

```python
# settings.json
{
  "permission": {
    "mode": "external",
    "external_provider": {
      "url": "https://your-system.com/permission-check",
      "timeout_seconds": 5,
      "cache_ttl_seconds": 300
    }
  }
}
```

**External Provider Request:**

```json
POST /permission-check
{
  "session_id": "abc123",
  "tool_name": "bash",
  "tool_input": { "command": "rm -rf /tmp/test" },
  "path": "/tmp/test",
  "user": "ci-bot"
}
```

**External Provider Response:**

```json
{
  "allowed": true,
  "reason": "Pre-approved for CI environment",
  "expires_at": "2026-06-09T19:00:00Z"
}
```

**Implementation:**
- New `ExternalPermissionProvider` class implementing `PermissionChecker` protocol
- HTTP POST with timeout and retry
- In-memory cache with TTL
- Fallback to `deny` if provider unreachable

**Priority:** P1 (High)
**Effort:** 1 week
**Dependencies:** Permission system (already exists)

---

#### 2.2.3 Task/Job Queue API
**Purpose:** Manage background tasks and scheduled jobs programmatically.

**Endpoints:**

```
POST /api/v1/tasks
  - Create background task
  - Parameters: { type: "query"|"script", payload: {...}, priority?: 1-10 }
  - Returns: { task_id, status: "queued" }

GET /api/v1/tasks
  - List tasks
  - Parameters: { status?, limit?, offset? }
  - Returns: { tasks: [...], total }

GET /api/v1/tasks/{task_id}
  - Get task status
  - Returns: { task_id, status, result?, error?, started_at?, completed_at? }

POST /api/v1/tasks/{task_id}/cancel
  - Cancel running task
  - Returns: { success }

POST /api/v1/jobs
  - Create scheduled job (cron)
  - Parameters: { name, cron_expression, payload: {...}, timezone? }
  - Returns: { job_id, next_run }

GET /api/v1/jobs
  - List scheduled jobs
  - Returns: { jobs: [...] }

PUT /api/v1/jobs/{job_id}
  - Update job
  - Parameters: { cron_expression?, payload?, enabled? }
  - Returns: { job_id, next_run }

DELETE /api/v1/jobs/{job_id}
  - Delete job

POST /api/v1/jobs/{job_id}/trigger
  - Manually trigger job
  - Returns: { task_id }
```

**Implementation:**
- Extend existing `BackgroundTaskManager` with HTTP API
- Extend existing `CronScheduler` with HTTP API
- Task queue with priority ordering
- Job execution creates tasks

**Priority:** P1 (High)
**Effort:** 1-2 weeks
**Dependencies:** Task manager, cron scheduler (already exist)

---

#### 2.2.4 Channel Management API
**Purpose:** Add/remove/configure channels at runtime.

**Endpoints:**

```
POST /api/v1/channels
  - Add channel
  - Parameters: { type: "telegram"|"discord"|..., config: {...} }
  - Returns: { channel_id, status }

GET /api/v1/channels
  - List channels
  - Returns: { channels: [...] }

GET /api/v1/channels/{channel_id}
  - Get channel status
  - Returns: { channel_id, type, status, message_count }

PUT /api/v1/channels/{channel_id}
  - Update channel config
  - Parameters: { config: {...} }
  - Returns: { success }

DELETE /api/v1/channels/{channel_id}
  - Remove channel

POST /api/v1/channels/{channel_id}/send
  - Send message via channel
  - Parameters: { chat_id, content, media? }
  - Returns: { message_id }

GET /api/v1/channels/{channel_id}/messages
  - Get recent messages
  - Parameters: { limit?, before?, after? }
  - Returns: { messages: [...] }
```

**Implementation:**
- Extend existing `ChannelManager` with HTTP API
- Dynamic channel instantiation from config
- Hot-reload on config change

**Priority:** P1 (High)
**Effort:** 1-2 weeks
**Dependencies:** Channel system (already exists)

---

### 2.3 Observability APIs (Medium Priority)

#### 2.3.1 Metrics & Monitoring API
**Purpose:** Expose metrics for monitoring and alerting.

**Endpoints:**

```
GET /api/v1/metrics
  - Usage metrics
  - Returns: {
      queries_total: 1000,
      queries_by_status: { success: 950, failed: 50 },
      tokens_used: { input: 500000, output: 250000 },
      cost_estimate_usd: 12.50,
      active_sessions: 5,
      uptime_seconds: 86400
    }

GET /api/v1/metrics/prometheus
  - Prometheus-compatible metrics
  - Returns: Prometheus text format

GET /api/v1/logs
  - Query logs
  - Parameters: { level?, since?, limit?, query? }
  - Returns: { logs: [...] }

GET /api/v1/health
  - Health check
  - Returns: {
      status: "ok"|"degraded"|"error",
      version: "0.1.9",
      uptime: 86400,
      checks: {
        api_client: "ok",
        memory: "ok",
        channels: "ok"
      }
    }
```

**Implementation:**
- Use `prometheus_client` library for metrics export
- Structured logging with `structlog` (add to dependencies)
- Health checks for all subsystems

**Priority:** P2 (Medium)
**Effort:** 1 week
**Dependencies:** None

---

#### 2.3.2 Audit Log API
**Purpose:** Track all operations for compliance and debugging.

**Endpoints:**

```
GET /api/v1/audit
  - Query audit log
  - Parameters: { action?, user?, since?, limit? }
  - Returns: { entries: [...] }

Audit Entry:
{
  "timestamp": "2026-06-09T18:00:00Z",
  "action": "tool.execute",
  "user": "api-key-abc123",
  "session_id": "abc123",
  "tool": "bash",
  "input": { "command": "ls" },
  "result": "success",
  "ip": "127.0.0.1"
}
```

**Implementation:**
- Intercept all tool executions, permission decisions, config changes
- Write to append-only log file (JSONL format)
- Queryable via API with filtering

**Priority:** P2 (Medium)
**Effort:** 1 week
**Dependencies:** None

---

### 2.4 Extension APIs (Low Priority)

#### 2.4.1 Plugin Management API
**Purpose:** Install/remove/enable/disable plugins at runtime.

**Endpoints:**

```
POST /api/v1/plugins
  - Install plugin
  - Parameters: { source: "path"|"url"|"registry", path?, url?, name? }
  - Returns: { plugin_id, name, version }

GET /api/v1/plugins
  - List plugins
  - Returns: { plugins: [...] }

PUT /api/v1/plugins/{plugin_id}
  - Update plugin
  - Parameters: { enabled?: bool }
  - Returns: { success }

DELETE /api/v1/plugins/{plugin_id}
  - Uninstall plugin

POST /api/v1/plugins/{plugin_id}/reload
  - Reload plugin
  - Returns: { success }
```

**Priority:** P3 (Low)
**Effort:** 1 week
**Dependencies:** Plugin system (already exists)

---

#### 2.4.2 Hook Management API
**Purpose:** Add/remove/modify hooks at runtime.

**Endpoints:**

```
POST /api/v1/hooks
  - Add hook
  - Parameters: { event, type, command?, url?, prompt?, matcher?, priority? }
  - Returns: { hook_id }

GET /api/v1/hooks
  - List hooks
  - Parameters: { event? }
  - Returns: { hooks: [...] }

PUT /api/v1/hooks/{hook_id}
  - Update hook
  - Parameters: { command?, url?, priority?, enabled? }
  - Returns: { success }

DELETE /api/v1/hooks/{hook_id}
  - Remove hook
```

**Priority:** P3 (Low)
**Effort:** 1 week
**Dependencies:** Hook system (already exists)

---

## 3. Implementation Phases

### Phase 1: Core API Infrastructure (Weeks 1-4)

**Goals:**
- HTTP API server with authentication
- WebSocket API for real-time streaming
- Python SDK for programmatic access
- Basic session and query management

**Deliverables:**
- `openharness/api/server.py` — FastAPI/Starlette HTTP server
- `openharness/api/websocket.py` — WebSocket handler
- `openharness/sdk/` — Python SDK package
- `openharness cli --api-server` flag to start API server
- API documentation (OpenAPI spec)
- Integration tests for all endpoints

**Success Criteria:**
- Can submit queries via HTTP and receive streaming responses
- Can create/resume/terminate sessions via API
- Python SDK works for all basic operations
- API server runs stably for 24+ hours

---

### Phase 2: Automation & Control (Weeks 5-8)

**Goals:**
- Permission management API
- Memory management API
- Configuration management API
- Webhook/event system
- External permission provider

**Deliverables:**
- Permission CRUD endpoints
- Memory CRUD endpoints
- Configuration GET/PUT endpoints
- Webhook dispatcher with retry logic
- `ExternalPermissionProvider` implementation
- CLI commands for API management (`openharness api ...`)

**Success Criteria:**
- Can pre-approve permissions for automated workflows
- Can add/search/remove memories via API
- Can change configuration at runtime
- Webhooks fire reliably on events
- External permission provider integrates with auth system

---

### Phase 3: Observability & Management (Weeks 9-12)

**Goals:**
- Metrics and monitoring
- Audit logging
- Task/job queue API
- Channel management API
- MCP server mode

**Deliverables:**
- Prometheus metrics endpoint
- Structured logging with queryable API
- Task queue with priority ordering
- Channel CRUD endpoints
- MCP server implementation
- Dashboard for monitoring (optional)

**Success Criteria:**
- Metrics export to Prometheus/Grafana
- Audit log tracks all operations
- Can create/monitor background tasks via API
- Can add/remove channels at runtime
- MCP clients can call OpenHarness tools

---

### Phase 4: Extensions & Polish (Weeks 13-16)

**Goals:**
- Plugin management API
- Hook management API
- API rate limiting
- API key management
- Documentation and examples

**Deliverables:**
- Plugin CRUD endpoints
- Hook CRUD endpoints
- Rate limiting middleware
- API key rotation and revocation
- Comprehensive API documentation
- Example integrations (CI/CD, IDE, chatbot)
- SDK examples and tutorials

**Success Criteria:**
- Can manage plugins/hooks via API
- API is protected against abuse
- Documentation is complete and accurate
- At least 3 real-world integration examples

---

## 4. Technical Design

### 4.1 API Server Architecture

```
┌─────────────────────────────────────────────────┐
│  External Clients (HTTP, WebSocket, SDK)        │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  API Server (FastAPI/Starlette)                 │
│  - Authentication (API key, OAuth)              │
│  - Rate limiting                                │
│  - Request validation (Pydantic)                │
│  - CORS support                                 │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  API Handlers                                   │
│  - QueryHandler → QueryEngine                   │
│  - SessionHandler → SessionStorage              │
│  - PermissionHandler → PermissionChecker        │
│  - MemoryHandler → MemoryManager                │
│  - ConfigHandler → Settings                     │
│  - TaskHandler → BackgroundTaskManager          │
│  - ChannelHandler → ChannelManager              │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│  Existing OpenHarness Core                      │
│  - Engine, Tools, Services, etc.                │
└─────────────────────────────────────────────────┘
```

### 4.2 Authentication & Authorization

**API Key Authentication:**
```python
# Generate API key
openharness api key create --name "CI Bot"

# Use API key
curl -H "Authorization: Bearer ohk_abc123..." http://localhost:8080/api/v1/health
```

**Permission Scopes:**
```python
# API key with limited scope
openharness api key create --name "Read Only" --scopes "read:session,read:memory"

# Scopes:
# - read:session, write:session
# - read:memory, write:memory
# - read:config, write:config
# - execute:query
# - manage:plugins, manage:hooks
# - admin (all)
```

### 4.3 Error Handling

**Standard Error Response:**
```json
{
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "Session abc123 does not exist",
    "details": { "session_id": "abc123" }
  }
}
```

**Error Codes:**
- `INVALID_REQUEST` — Malformed request
- `UNAUTHORIZED` — Missing or invalid API key
- `FORBIDDEN` — Insufficient permissions
- `NOT_FOUND` — Resource not found
- `CONFLICT` — Resource already exists
- `RATE_LIMITED` — Too many requests
- `INTERNAL_ERROR` — Server error

### 4.4 Configuration

**New settings.json fields:**
```json
{
  "api_server": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 8080,
    "api_key": "ohk_...",
    "cors_origins": ["*"],
    "rate_limit": {
      "requests_per_minute": 60,
      "burst": 10
    }
  },
  "webhooks": [
    {
      "url": "https://...",
      "events": ["query.completed"],
      "secret": "..."
    }
  ],
  "permission": {
    "mode": "external",
    "external_provider": {
      "url": "https://...",
      "timeout_seconds": 5
    }
  }
}
```

---

## 5. Testing Strategy

### 5.1 Unit Tests
- Test each API handler in isolation
- Mock dependencies (QueryEngine, SessionStorage, etc.)
- Validate request/response schemas

### 5.2 Integration Tests
- Test API server with real QueryEngine
- Test WebSocket streaming
- Test authentication and authorization
- Test error handling and edge cases

### 5.3 End-to-End Tests
- Test full workflow: create session → submit query → get response
- Test concurrent sessions
- Test long-running queries
- Test error recovery

### 5.4 Performance Tests
- Load test API server (1000 req/s)
- Stress test WebSocket connections (100 concurrent)
- Measure latency (p50, p95, p99)

---

## 6. Documentation

### 6.1 API Reference
- OpenAPI 3.0 specification
- Auto-generated from FastAPI
- Hosted at `/docs` (Swagger UI) and `/redoc`

### 6.2 SDK Documentation
- Docstrings for all methods
- Usage examples
- Type hints

### 6.3 Integration Guides
- "Integrating with CI/CD Pipelines"
- "Building a Custom IDE Plugin"
- "Connecting to Chat Platforms"
- "Automating Workflows with Python SDK"

---

## 7. Migration Path

### 7.1 Backward Compatibility
- All existing CLI commands continue to work
- No breaking changes to existing APIs
- API server is opt-in (disabled by default)

### 7.2 Deprecation Policy
- CLI commands that duplicate API functionality marked as deprecated after 6 months
- 12-month deprecation window before removal
- Migration guides provided

---

## 8. Success Metrics

### 8.1 Adoption Metrics
- Number of API keys created
- API requests per day
- SDK downloads
- Integration examples contributed

### 8.2 Performance Metrics
- API latency (p50 < 100ms, p99 < 500ms)
- API availability (>99.9%)
- WebSocket connection stability (<1% disconnect rate)

### 8.3 Quality Metrics
- API test coverage (>90%)
- Documentation completeness (100% of endpoints documented)
- Bug report rate (<1 per 1000 requests)

---

## 9. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| API server introduces security vulnerabilities | High | Medium | Security audit, rate limiting, input validation, API key scoping |
| Performance degradation under load | High | Medium | Load testing, connection pooling, async I/O, caching |
| Breaking changes to existing functionality | High | Low | Comprehensive test suite, backward compatibility, deprecation policy |
| Scope creep delays delivery | Medium | High | Strict prioritization, phased delivery, MVP focus |
| Insufficient documentation hinders adoption | Medium | Medium | Documentation-first approach, examples for all endpoints |

---

## 10. Conclusion

This proposal defines a comprehensive API surface to enable fully headless operation of OpenHarness. The implementation is phased over 16 weeks, with critical APIs delivered in the first 4 weeks.

**Key Benefits:**
- Enables integration with CI/CD pipelines, IDEs, chat platforms, and custom workflows
- Eliminates manual intervention for automated use cases
- Provides observability for monitoring and debugging
- Maintains backward compatibility with existing CLI

**Next Steps:**
1. Review and approve proposal
2. Assign implementation team
3. Set up development environment
4. Begin Phase 1 implementation

---

## Appendix A: Example Integrations

### A.1 CI/CD Pipeline Integration

```yaml
# .github/workflows/code-review.yml
name: AI Code Review
on: [pull_request]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Start OpenHarness
        run: |
          openharness --api-server --port 8080 &
          sleep 5

      - name: Review PR
        run: |
          python review.py
        env:
          OPENHARNESS_API_URL: http://localhost:8080
          OPENHARNESS_API_KEY: ${{ secrets.OH_API_KEY }}
```

```python
# review.py
from openharness.sdk import OpenHarnessClient
import os

client = OpenHarnessClient(
    api_url=os.environ["OPENHARNESS_API_URL"],
    api_key=os.environ["OPENHARNESS_API_KEY"]
)

session = client.create_session(permission_mode="full_auto")
result = session.query("Review the changes in this PR and provide feedback")

# Post result as PR comment
post_pr_comment(result.text)
```

### A.2 IDE Plugin Integration

```typescript
// VS Code extension
import { OpenHarnessClient } from 'openharness-sdk-js';

const client = new OpenHarnessClient({
  apiUrl: 'http://localhost:8080',
  apiKey: 'ohk_...'
});

// Submit query from IDE
async function askOpenHarness(prompt: string) {
  const session = await client.createSession();
  const stream = session.queryStream(prompt);

  for await (const event of stream) {
    if (event.type === 'text_delta') {
      appendToOutput(event.text);
    }
  }
}
```

### A.3 Chat Platform Integration

```python
# Slack bot
from slack_bolt import App
from openharness.sdk import OpenHarnessClient

app = App(token=os.environ["SLACK_BOT_TOKEN"])
oh_client = OpenHarnessClient(api_url="http://localhost:8080", api_key="ohk_...")

@app.message(".*")
def handle_message(message, say):
    session = oh_client.create_session()
    result = session.query(message["text"])
    say(result.text)
```

---

## Appendix B: API Endpoint Summary

| Category | Endpoints | Priority |
|----------|-----------|----------|
| **Query** | POST /query, GET /query/{id}/stream | P0 |
| **Session** | POST /session, GET /session/{id}, POST /session/{id}/resume, DELETE /session/{id}, GET /sessions | P0 |
| **Permission** | POST /permission/approve, GET /permissions, DELETE /permission/{id} | P0 |
| **Memory** | POST /memory, GET /memory, DELETE /memory/{id}, GET /memory/entrypoint | P0 |
| **Config** | GET /config, PUT /config | P1 |
| **Health** | GET /health, GET /metrics | P1 |
| **Webhook** | POST /webhook, GET /webhooks, DELETE /webhook/{id} | P1 |
| **Task** | POST /tasks, GET /tasks, GET /tasks/{id}, POST /tasks/{id}/cancel | P1 |
| **Job** | POST /jobs, GET /jobs, PUT /jobs/{id}, DELETE /jobs/{id}, POST /jobs/{id}/trigger | P1 |
| **Channel** | POST /channels, GET /channels, GET /channels/{id}, PUT /channels/{id}, DELETE /channels/{id}, POST /channels/{id}/send, GET /channels/{id}/messages | P1 |
| **Audit** | GET /audit | P2 |
| **Plugin** | POST /plugins, GET /plugins, PUT /plugins/{id}, DELETE /plugins/{id}, POST /plugins/{id}/reload | P3 |
| **Hook** | POST /hooks, GET /hooks, PUT /hooks/{id}, DELETE /hooks/{id} | P3 |

**Total: 37 endpoints**
