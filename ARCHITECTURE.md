# MSP Tools — System Architecture

## Overview

This document describes the internal architecture of the MSP Tools RMM/PSA platform, with focus on the Go-based server and agent components. It covers data flow, concurrency models, deployment topology, and key design decisions.

---

## System Layers

```
┌──────────────────────────────────────────────────────────┐
│                    Presentation Layer                     │
│         (Web UI — React SPA + Technician CLI)            │
└───────────────────────┬──────────────────────────────────┘
                        │ HTTPS / WSS
┌───────────────────────▼──────────────────────────────────┐
│                    API Layer (Gin)                       │
│  Middleware: ID → Log → Recover → RateLimit → Auth → CORS│
│  Routes: /api/* (REST) + /ws/* (WebSocket)               │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                   Service Layer                          │
│  AgentService | MonitoringService | AlertingService     │
│  TicketService | BillingService                          │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                   Data Layer                             │
│  Repository pattern (interfaces + SQLite/PostgreSQL)     │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                   Storage                                │
│           SQLite (dev) / PostgreSQL (prod)              │
└──────────────────────────────────────────────────────────┘
```

---

## Server Architecture (`server/`)

### Concurrency Model

Go's goroutine-per-connection handles agent connections efficiently. Each WebSocket connection spawns a single goroutine that reads messages, processes them, and writes responses. Goroutines are lightweight (2 KB initial stack, grows on demand), and the Go scheduler handles multiplexing onto OS threads (GOMAXPROCS = number of CPU cores by default).

**Key goroutine sources:**
- HTTP request handlers (one goroutine per request; Gin pools them)
- WebSocket connections (one goroutine per agent + terminal session)
- Scheduled check execution (via `cron` or in-process ticker)
- Alert evaluation (triggered on new check results)
- Background cleanup (dead connection removal, metric retention)

**Memory footprint estimate:**
- 1000 agents connected: ~50–100 MB total (vs. 1–2 GB with Python + async frameworks)
- Each agent goroutine: ~5–10 KB stack + WebSocket buffer overhead

### WebSocket Hub (`server/ws/hub.go`)

The hub is the central dispatcher for all WebSocket connections.

```
Hub
 ├── clients: map[*Client]bool   (registered connections)
 ├── groups:  map[string][]*Client (e.g. "client:42" → all endpoints for client 42)
 ├── register:   chan *Client
 ├── unregister:  chan *Client
 ├── broadcast:   chan []byte
 └── mutex (protects maps during concurrent modification)
```

**Client struct (per connection):**
```go
type Client struct {
    hub      *Hub
    conn     *websocket.Conn
    send     chan []byte      // buffered channel for outgoing messages
    endpoint *Endpoint        // nil for terminal sessions
    user     *User            // nil for agent connections
    isAgent  bool
}
```

**Message flow:**
1. Client connects → `hub.register` → client added to `clients` map and relevant `groups`
2. Incoming message → goroutine reads from WebSocket → parsed → routed to handler
3. Outgoing message → goroutine sends to `client.send` channel → WebSocket write
4. Disconnect → `hub.unregister` → cleanup (remove from groups, close send channel)

**Heartbeat:** Hub pings all clients every 30s. Clients must pong within 30s or are disconnected.

### Request Lifecycle

```
HTTP Request
    │
    ▼
Gin Middleware Chain
    ├── RequestID    — attach UUID to context
    ├── Logger       — structured log (start time → duration → status)
    ├── Recoverer    — catch panics → 500 + stack
    ├── RateLimit    — token bucket (100/min IP, 1000/min JWT)
    ├── Auth         — JWT validation → inject user into context
    ├── CORS         — set Access-Control-* headers
    └── RBAC         — check role permissions (if route requires specific role)
    │
    ▼
Handler
    │
    ▼
Service (business logic)
    │
    ▼
Repository (database)
    │
    ▼
Response (JSON) + Audit Log entry written asynchronously
```

### Database Migrations (`server/db/migrate.go`)

Migrations are versioned SQL files applied sequentially on startup.

```
schema_migrations table:
  version | applied_at
  1       | 2024-01-15 10:00:00Z
  2       | 2024-01-20 14:30:00Z
```

Migration files live in `server/db/migrations/` and are applied in order. Rollback is not supported (restore from backup instead).

### Repository Pattern

Each domain entity has a repository interface and a concrete implementation:

```go
type ClientRepository interface {
    Create(ctx context.Context, c *Client) error
    GetByID(ctx context.Context, id int64) (*Client, error)
    List(ctx context.Context, f ClientFilter) ([]*Client, error)
    Update(ctx context.Context, c *Client) error
    Delete(ctx context.Context, id int64) error
}

type SQLiteClientRepository struct {
    db *sql.DB
}
```

This keeps service layer independent of the database driver, making it straightforward to swap SQLite for PostgreSQL without changing business logic.

---

## Agent Architecture (`agent/`)

### Agent Binary

The agent is a **statically compiled Go binary** with no external runtime dependencies. It is embedded in the server binary and streamed to endpoints on first deployment.

**Agent responsibilities:**
1. Maintain persistent WebSocket connection to server
2. Send heartbeat with system metrics every 30s
3. Receive and execute scripts (PowerShell on Windows, Bash on Linux/macOS)
4. Run scheduled checks locally (disk, SSL, uptime — Python scripts invoked as subprocess)
5. Auto-update when server pushes a new version

### Agent Communication Flow

```
Agent Start
    │
    ├── First run? ──Yes──→ POST /api/auth/agent-register → JWT
    │                                              ↓
    │                    Persist JWT to local config file
    │
    ▼
WS Connect to /ws/agent/:token
    │
    ├── Server accepts → agent ready
    │
    ▼
Heartbeat Loop (every 30s)
    │
    ├── Send: { type: "heartbeat", metrics: {...} }
    ├── Recv: { type: "ack", pending_scripts: [...] }
    │
    ▼
Script Execution (on demand)
    │
    ├── Recv: { type: "execute_script", script_id: "uuid", script: "..." }
    ├── Execute locally (subprocess)
    ├── Send: { type: "script_result", script_id: "uuid", exit_code: X, output: "..." }
    │
    ▼
Auto-Update (on server push)
    │
    ├── Recv: { type: "update_push", version: "1.2.0", sha256: "..." }
    ├── Download /ws/agent/binary?version=1.2.0
    ├── Verify SHA-256
    ├── Mark for restart on next idle window
```

### Agent Deployment

The server exposes a download endpoint per platform:

```
GET /api/agents/windows   → Windows x86_64 binary
GET /api/agents/linux     → Linux x86_64 binary
GET /api/agents/darwin    → macOS x86_64 binary
```

The binary is embedded in the server as a `map[string][]byte` using `go:embed`. The server sends the correct binary based on the requesting agent's reported OS.

**Windows service registration:**
- Agent writes itself to `C:\Program Files\MSP Tools Agent\msp-agent.exe`
- Registers as a Windows Service via `sc.exe create`
- Runs as `LocalSystem` (or a dedicated service account)

**Linux/macOS service registration:**
- Writes to `/usr/local/bin/msp-agent`
- Creates systemd unit file (or launchd plist on macOS)
- Enables and starts the service

### Script Execution Sandbox

- **Windows:** PowerShell run in Constrained Language Mode; execution policy restricted
- **Linux/macOS:** Scripts run as the agent user (not root); explicit allowlist of dangerous commands not enforced (Go process limit would be needed for hard sandboxing)
- All script output is captured and returned to the server
- Execution timeout: 5 minutes (configurable per script invocation)

---

## Web UI Architecture (`web/`)

### Stack

- **React 18** with concurrent features
- **TypeScript** (strict mode)
- **Vite** (dev server + build)
- **React Router v6** (SPA routing)
- **TanStack Query v5** (server state management, caching, background refetch)
- **Zustand** (lightweight client-side state: sidebar, theme, auth)
- **Tailwind CSS** + **shadcn/ui** (styling + accessible components)
- **WebSocket provider** (top-level React context, reconnects automatically)

### Data Flow

```
User Action
    │
    ▼
React Component → TanStack Query mutation or fetch
    │
    ▼
API Request (HTTPS) → Server (REST API)
    │
    ▼
Response → TanStack Query cache update → React re-render
```

For live data (metrics, alerts):
```
Server (WebSocket) → WebSocket Provider → React Context → Subscribing Components
```

### API Client

Generated TypeScript types from Go server structs (using `swagger` or `oapi-codegen`). All API calls go through a typed client:

```typescript
const client = new ApiClient(baseUrl, token);
const endpoints = await client.endpoints.list({ client_id: 42 });
```

---

## Security Architecture

### TLS

All production deployments require TLS. The server terminates TLS directly (no reverse proxy required, though one can be placed in front). Certificate rotation is manual (reload server to pick up new cert).

### JWT Flow

```
Login:
  POST /api/auth/login { username, password }
    → Verify bcrypt hash
    → Generate access token (1h) + refresh token (7d)
    → Return both; store refresh token in httpOnly cookie

Access Token Usage:
  Authorization: Bearer <access_token>
    → Middleware validates signature + expiry
    → Inject user into Gin context

Refresh:
  POST /api/auth/refresh
    → Validate refresh token (from cookie)
    → Issue new access token
    → Return new access token only

Agent Token:
  POST /api/auth/agent-register { registration_key, hostname, os }
    → Verify global registration key
    → Issue agent-specific JWT (30d, scoped to endpoint)
    → Agent uses WS /ws/agent/:token (no Bearer header)
```

### Role-Based Access Control

| Resource | admin | technician | viewer |
|----------|-------|------------|--------|
| Clients | CRUD | CRUD | Read |
| Endpoints | CRUD | CRUD | Read |
| Checks | CRUD | CRUD | Read |
| Alerts | CRUD | Acknowledge | Read |
| Tickets | CRUD | CRUD (assigned) | Read |
| Time Entries | CRUD | CRUD (own) | Read |
| Knowledge Base | CRUD | CRUD | Read |
| Contracts | CRUD | Read | Read |
| Invoices | CRUD | None | Read |
| Reports | Full | Full | Read |
| Users | CRUD | None | None |

### Audit Log

Every write operation (POST, PUT, DELETE) is logged asynchronously:

```go
// Logged fields:
{ user_id, action, resource, resource_id, details (JSON), ip_address, created_at }
// Example:
{ 3, "DELETE", "endpoint", 17, `{"hostname": "server.acme.com"}`, "192.168.1.50", "2024-01-15T10:30:00Z" }
```

---

## Deployment Topology

### Single-Server (SQLite)

```
┌─────────────────────────────────┐
│          Ubuntu/Debian          │
│                                 │
│  ┌───────────────────────────┐  │
│  │  msp-tools server (Go)   │  │
│  │  :8080 (HTTP)            │  │
│  │  :8443 (HTTPS)           │  │
│  └───────────────────────────┘  │
│                                 │
│  ┌───────────────────────────┐  │
│  │  SQLite DB                │  │
│  │  ./data/msp-tools.db      │  │
│  └───────────────────────────┘  │
│                                 │
│  ┌───────────────────────────┐  │
│  │  React SPA (served by Go)  │  │
│  │  or nginx reverse proxy    │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘

Agents ──WSS──► :8443
Technicians ──HTTPS──► :8443
```

Suitable for: up to ~500 endpoints, moderate check frequency.

### Multi-Server (PostgreSQL)

```
                        ┌──────────────┐
                        │  PostgreSQL  │
                        │  (primary)   │
                        └──────┬───────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
  ┌──────▼───────┐     ┌──────▼───────┐     ┌──────▼───────┐
  │  Go Server   │     │  Go Server   │     │  Go Server   │
  │  (US-East)   │     │  (EU-West)   │     │  (AP-South)  │
  └──────────────┘     └──────────────┘     └──────────────┘

  All servers: same PostgreSQL, agents distributed geographically.
  TLS terminates at each server (or at load balancer in front).
```

Suitable for: thousands of endpoints, multiple office locations, high availability.

### Behind a Reverse Proxy

If placing Go server behind nginx/Caddy:

```
nginx/Caddy
    │
    ├── /api/*       → http://localhost:8080
    ├── /ws/agent/*  → ws://localhost:8080 (with proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade)
    └── /            → SPA static files (or separate nginx location for /web/dist)
```

Ensure `proxy_read_timeout 86400;` for long-lived WebSocket connections.

---

## Performance Characteristics

### Agent Connection Capacity

Each agent WebSocket connection consumes approximately:
- **Memory:** ~50–100 KB (goroutine stack + WebSocket buffers)
- **CPU:** Minimal when idle (just heartbeat ping/pong every 30s)

A single server with 4 CPU cores can handle:
- **5,000–10,000** concurrent agent connections
- **500–1,000** concurrent technician WebSocket sessions (live dashboard)

### Database Performance

**SQLite:**
- WAL mode allows concurrent readers; writes are serialised
- Suitable for ≤500 endpoints with moderate check frequency (every 5 min)
- Use `PRAGMA busy_timeout = 5000` to avoid "database locked" errors

**PostgreSQL:**
- Connection pool (default 25 connections, configurable up to 100)
- Indexes on: `endpoints(client_id)`, `checks(endpoint_id, executed_at)`, `tickets(status, assigned_to)`
- Metric retention: configurable (default 30 days for detailed, 1 year for hourly aggregates)

### API Latency

- p50: ~2–5ms (simple CRUD)
- p99: ~20–50ms (report generation, complex queries)
- WebSocket round-trip (script execution): depends on script duration

---

## Monitoring & Observability

### Structured Logging

All server logs are structured JSON (Zerolog or slog in Go 1.21+):

```json
{
  "level": "info",
  "ts": "2024-01-15T10:30:00Z",
  "request_id": "uuid",
  "method": "GET",
  "path": "/api/endpoints",
  "status": 200,
  "duration_ms": 4,
  "user_id": 3,
  "ip": "192.168.1.50"
}
```

### Metrics

Exposed at `/metrics` (Prometheus format):
- HTTP request count/latency by route
- WebSocket connection count (by type: agent, terminal)
- Active agents online/offline
- Check execution count by type and result
- Alert count by severity
- Database query latency

### Health Checks

```
GET /health           → 200 OK (server is up)
GET /health/ready     → 200 OK if DB is reachable + agents can be contacted
GET /health/live      → 200 OK (basic liveness, no DB check)
```

Kubernetes probes: `/health/live` for liveness, `/health/ready` for readiness.

---

## Directory Structure

```
msp-tools/
├── SPEC.md
├── ARCHITECTURE.md              ← this file
├── README.md
│
├── server/                      # Go API server
│   ├── main.go                  # Entry point, graceful shutdown, signal handling
│   ├── go.mod
│   │
│   ├── api/                    # HTTP handlers + routing
│   │   ├── routes.go           # Route group registration
│   │   ├── middleware/
│   │   │   ├── requestid.go    # UUID injection
│   │   │   ├── logger.go       # Structured request logging
│   │   │   ├── recoverer.go    # Panic recovery
│   │   │   ├── ratelimit.go    # Token bucket rate limiter
│   │   │   ├── auth.go         # JWT validation
│   │   │   ├── cors.go         # CORS headers
│   │   │   └── rbac.go         # Role-based permission check
│   │   ├── auth.go
│   │   ├── clients.go
│   │   ├── endpoints.go
│   │   ├── monitoring.go
│   │   ├── tickets.go
│   │   ├── time.go
│   │   ├── kb.go
│   │   ├── contracts.go
│   │   ├── billing.go
│   │   ├── reports.go
│   │   └── agents.go           # Binary download endpoints
│   │
│   ├── db/                     # Database layer
│   │   ├── db.go               # SQLite / PostgreSQL connection
│   │   ├── migrate.go          # Versioned migration runner
│   │   ├── database.go         # Database interface definition
│   │   ├── sqlite/
│   │   │   ├── sqlite.go       # SQLite implementation
│   │   │   ├── client.go
│   │   │   ├── endpoint.go
│   │   │   ├── ticket.go
│   │   │   └── ...
│   │   ├── postgres/
│   │   │   ├── postgres.go      # PostgreSQL implementation
│   │   │   ├── client.go
│   │   │   └── ...
│   │   └── migrations/
│   │       ├── 001_initial.sql
│   │       ├── 002_add_audit.sql
│   │       └── ...
│   │
│   ├── services/              # Business logic (database-agnostic)
│   │   ├── agent.go           # Agent registration, token management
│   │   ├── monitoring.go      # Check scheduling, result aggregation
│   │   ├── alerting.go       # Rule evaluation, notification dispatch
│   │   ├── ticket.go         # Ticket lifecycle, SLA tracking
│   │   ├── billing.go        # Invoice generation, payment tracking
│   │   └── auth.go           # Login, token refresh, password hashing
│   │
│   ├── ws/                    # WebSocket layer
│   │   ├── hub.go             # Connection hub (register/unregister/broadcast)
│   │   ├── client.go          # Client struct + read/write goroutines
│   │   ├── agent.go           # Agent message handlers (heartbeat, script result, etc.)
│   │   ├── terminal.go        # Interactive shell terminal WS handler
│   │   └── live.go            # Live data stream (metrics, alerts) WS handler
│   │
│   ├── embed/                 # Embedded agent binary (go:embed)
│   │   └── agent_binaries.go  # Maps platform → binary bytes
│   │
│   └── config/
│       ├── config.go          # YAML config loader
│       └── config_test.go
│
├── agent/                      # Agent source (built separately)
│   ├── main.go
│   ├── client/
│   │   └── ws.go             # WebSocket client with auto-reconnect
│   ├── checks/
│   │   ├── disk.go
│   │   ├── ssl.go
│   │   └── uptime.go
│   ├── scripts/
│   │   └── executor.go       # Script runner (PowerShell/Bash)
│   ├── updater/
│   │   └── updater.go        # Auto-update logic
│   ├── service/
│   │   └── service.go        # OS service registration
│   └── README.md
│
├── web/                       # React SPA
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── tailwind.config.js
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/              # Generated typed API client
│   │   ├── components/       # Reusable UI components (shadcn)
│   │   ├── pages/            # Route-level page components
│   │   ├── hooks/            # Custom React hooks
│   │   ├── lib/              # Utilities
│   │   ├── store/            # Zustand stores
│   │   └── types/            # TypeScript type definitions
│   └── dist/                 # Built static assets (served by Go server)
│
├── cli/                       # Technician CLI (Go + Cobra)
│   ├── main.go
│   ├── cmd/
│   │   ├── login.go
│   │   ├── client.go
│   │   ├── endpoint.go
│   │   ├── ticket.go
│   │   └── ...
│   └── go.mod
│
├── monitoring/                 # Existing Python scripts (retained)
│   ├── check-disk.py
│   ├── check-ssl.py
│   └── check-uptime.py
│
├── automation/                 # Runbook definitions (YAML)
│   └── runbooks/
│       ├── restart-service.yaml
│       └── clear-temp.yaml
│
├── psa/                        # PSA module (Python, existing)
│   ├── tickets/
│   ├── time/
│   ├── kb/
│   ├── contracts/
│   └── billing/
│
├── data/                       # Runtime data
│   └── msp-tools.db           # SQLite database (gitignored)
│
├── logs/                       # Server logs (gitignored)
│   └── .gitkeep
│
└── config/                     # Configuration (gitignored)
    └── config.yaml.example
```

---

## Design Decisions

### Go over Python for Server/Agent

- **Concurrency:** Go goroutines handle thousands of concurrent WebSocket connections with minimal memory. Python async (asyncio) works but requires more care to avoid blocking the event loop, and the ecosystem is less mature for this use case.
- **Single binary deployment:** Go compiles to a static binary with no runtime. Python requires interpreter + dependencies on each endpoint.
- **Cross-compilation:** Build Windows agent from Linux in one command (`GOOS=windows GOARCH=amd64 go build`). No CI VMs needed.
- **Type safety:** Compile-time type checking catches entire classes of bugs that Python only catches at runtime.
- **Performance:** Go handles JSON parsing/serialisation, TLS, and HTTP/WS with lower latency and less GC pressure than Python.

### Gin over Standard `net/http`

Gin provides a well-tuned HTTP router (3–5x faster than the standard library in benchmarks) and a middleware system that integrates cleanly with the Go ecosystem. The performance difference is rarely the bottleneck, but developer ergonomics (ctx-based request handling, binding/validation helpers) save significant time.

### SQLite for Single-Server, PostgreSQL for Scale

SQLite with WAL mode handles 500 concurrent endpoints well and requires zero administration. When the MSP outgrows a single server, migrate to PostgreSQL without changing application code (just swap the repository implementation and update the DSN).

### WebSocket for Agent Communication

Agents maintain a persistent WebSocket connection rather than polling HTTP. This gives:
- Real-time script execution (no polling delay)
- Lower server load (no thousands of polling requests/minute)
- Immediate alert delivery to dashboards

### HTMX vs React

The spec originally planned HTMX + Go templates for a simpler, Node-free deployment. We chose React + Vite because:
- React's component model scales better as the UI grows in complexity
- TanStack Query provides a superior developer experience for data fetching/caching
- A modern SPA is expected for an RMM product; HTMX would feel dated
- Vite's dev experience is fast enough that the Node.js dependency is acceptable

### JWT over Session Cookies for API

JWTs are stateless and work cleanly across multiple API servers (PostgreSQL-backed sessions would require a shared session store). The tradeoff is that JWTs cannot be revoked before expiry — we mitigate this with short access token TTL (1h) and a refresh token rotation scheme.

### No ORM

Raw SQL via `database/sql` + `sqlx.Keep in mind this is what was decided:`. ORMs hide too much control (index usage, query plans, transaction boundaries). For a platform where DB performance directly impacts monitoring latency, explicit SQL is the right choice.

### Agent Embedded in Server Binary

The agent binary is embedded in the server binary using `go:embed` and served via a download endpoint. This means:
- Single deployment artifact (one `msp-tools` binary contains server + all agent binaries)
- No external file server needed
- Version alignment enforced automatically (server version = agent version)

The tradeoff: server binary is larger (~50–100 MB with all agent binaries included). Acceptable for a server deployment.
