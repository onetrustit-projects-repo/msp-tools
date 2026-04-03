# MSP Tools Suite — Production Specification

## Overview

A production-grade, self-hosted Remote Monitoring & Management (RMM) and Professional Services Automation (PSA) platform built entirely in Go. Designed for Managed Service Providers who want full control over their stack — no per-endpoint SaaS fees, no vendor lock-in.

**Core Language:** Go 1.21+  
**Agent Language:** Go (cross-compiled to Windows, Linux, macOS)  
**Database:** SQLite (single-server, ≤500 endpoints) or PostgreSQL (multi-server, thousands of endpoints)  
**Web UI:** React 18 + Vite (TypeScript)  
**Auth:** JWT (1h access tokens, 7d refresh tokens), TLS everywhere  
**WebSocket:** gorilla/websocket for agent and terminal connections  

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────┐
│                    Technicians                          │
│              (Web UI + Technician CLI)                 │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTPS/WSS
┌─────────────────────▼───────────────────────────────────┐
│                   Go API Server                        │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │
│  │  REST API   │ │  WebSocket │ │  Agent Hub      │  │
│  │  (Gin)      │ │  (gorilla) │ │  (goroutines)   │  │
│  └─────────────┘ └─────────────┘ └─────────────────┘  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐  │
│  │  Services   │ │  Scheduler  │ │  Alert Engine   │  │
│  └─────────────┘ └─────────────┘ └─────────────────┘  │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│              SQLite / PostgreSQL                        │
└─────────────────────────────────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         │            │            │
   ┌─────▼─────┐ ┌────▼────┐ ┌────▼────┐
   │  Go Agent │ │  Go Agent│ │  Go Agent│
   │ (Windows) │ │ (Linux)  │ │ (macOS)  │
   └───────────┘ └──────────┘ └──────────┘
```

---

## 1. Server (`server/`)

### 1.1 Entry Point — `server/main.go`

- Reads configuration from `config/config.yaml`
- Sets up graceful shutdown (SIGINT/SIGTERM → drain connections)
- Starts HTTP server on `:8080` (or configured port)
- Starts WebSocket hub for agent connections
- Initializes database migrations
- Registers all routes

**Graceful Shutdown:**
- Stops accepting new connections
- Waits up to 30s for in-flight requests
- Closes WebSocket connections cleanly
- Flushes logs

### 1.2 API Layer (`server/api/`)

**Routing:** Gin framework with grouped routes.

**Middleware Stack (per request):**
1. Request ID (UUID, propagated to logs)
2. Logger (structured JSON logs → stdout/files)
3. Recoverer (panic → 500 + stack trace)
4. Rate limiter (100 req/min per IP, 1000 req/min per JWT)
5. Authenticator (JWT validation on protected routes)
6. CORS (configurable allowed origins)
7. TLS redirect (HTTP → HTTPS in production)

**Route Groups:**
- `/api/auth` — login, token refresh, agent registration
- `/api/clients` — client CRUD
- `/api/endpoints` — endpoint CRUD, script execution, metrics
- `/api/checks` — check definitions, scheduling
- `/api/alerts` — alert listing, acknowledgment
- `/api/tickets` — ticket CRUD, comments, status transitions
- `/api/time-entries` — time tracking CRUD
- `/api/kb` — knowledge base CRUD
- `/api/contracts` — contract CRUD
- `/api/invoices` — invoice CRUD, status updates
- `/api/reports` — health, SLA, technician, monthly reports
- `/ws/agent/:token` — agent WebSocket (long-lived)
- `/ws/terminal/:id` — remote terminal session
- `/ws/live/:type` — live metrics/alerts stream

**Response Format:**
```json
{
  "request_id": "uuid",
  "data": { },
  "error": null,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Error Format:**
```json
{
  "request_id": "uuid",
  "data": null,
  "error": {
    "code": "CLIENT_NOT_FOUND",
    "message": "Client with ID 42 not found"
  },
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### 1.3 Database Layer (`server/db/`)

**SQLite (development / ≤500 endpoints):**
- Single file: `data/msp-tools.db`
- WAL mode enabled (concurrent reads, serialised writes)
- Foreign keys enforced
- Migrations run on startup (versioned via `schema_migrations` table)

**PostgreSQL (production / thousands of endpoints):**
- Connection pool: 25 connections default, configurable
- Supports read replicas for reporting queries
- Same schema as SQLite

**Schema (all tables):**

```sql
-- Core
clients (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  contact_name TEXT,
  contact_email TEXT,
  contact_phone TEXT,
  address TEXT,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
)

endpoints (
  id SERIAL PRIMARY KEY,
  client_id INTEGER REFERENCES clients(id),
  hostname TEXT NOT NULL,
  fqdn TEXT,
  ip_address TEXT,
  mac_address TEXT,
  os_name TEXT,
  os_version TEXT,
  agent_version TEXT,
  last_seen TIMESTAMPTZ,
  status TEXT DEFAULT 'offline', -- online|offline|warning|critical
  created_at TIMESTAMPTZ DEFAULT NOW()
)

endpoint_metrics (
  id SERIAL PRIMARY KEY,
  endpoint_id INTEGER REFERENCES endpoints(id),
  metric_type TEXT NOT NULL, -- cpu|memory|disk|network|process
  value REAL NOT NULL,
  unit TEXT,
  collected_at TIMESTAMPTZ DEFAULT NOW()
)

checks (
  id SERIAL PRIMARY KEY,
  endpoint_id INTEGER REFERENCES endpoints(id),
  check_type TEXT NOT NULL, -- disk|ssl|uptime|memory|cpu|service|backup
  status TEXT NOT NULL, -- ok|warning|critical|unknown
  output TEXT,
  executed_at TIMESTAMPTZ DEFAULT NOW()
)

-- PSA: Tickets
tickets (
  id SERIAL PRIMARY KEY,
  client_id INTEGER REFERENCES clients(id),
  endpoint_id INTEGER REFERENCES endpoints(id),
  title TEXT NOT NULL,
  description TEXT,
  status TEXT DEFAULT 'open', -- open|in_progress|pending|resolved|closed
  priority TEXT DEFAULT 'medium', -- low|medium|high|critical
  assigned_to TEXT,
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  closed_at TIMESTAMPTZ
)

ticket_comments (
  id SERIAL PRIMARY KEY,
  ticket_id INTEGER REFERENCES tickets(id),
  author TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
)

-- PSA: Time Tracking
time_entries (
  id SERIAL PRIMARY KEY,
  ticket_id INTEGER REFERENCES tickets(id),
  user TEXT NOT NULL,
  minutes INTEGER NOT NULL,
  description TEXT,
  billable BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
)

-- PSA: Knowledge Base
knowledge_base (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  category TEXT,
  tags TEXT[], -- PostgreSQL array; JSON array in SQLite
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
)

-- PSA: Contracts
contracts (
  id SERIAL PRIMARY KEY,
  client_id INTEGER REFERENCES clients(id),
  name TEXT NOT NULL,
  contract_type TEXT, -- break_fix|retainer|project
  start_date DATE,
  end_date DATE,
  monthly_value REAL DEFAULT 0,
  notes TEXT
)

-- PSA: Billing
invoices (
  id SERIAL PRIMARY KEY,
  client_id INTEGER REFERENCES clients(id),
  invoice_number TEXT UNIQUE NOT NULL,
  amount REAL NOT NULL,
  status TEXT DEFAULT 'draft', -- draft|sent|paid|overdue|cancelled
  issued_at TIMESTAMPTZ,
  due_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ
)

-- Alerting
alert_rules (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  condition TEXT NOT NULL, -- JSON condition expression
  action TEXT NOT NULL, -- email|webhook|pagerduty
  action_config TEXT, -- JSON config for action
  enabled BOOLEAN DEFAULT true,
  endpoint_id INTEGER REFERENCES endpoints(id), -- NULL = all endpoints
  created_at TIMESTAMPTZ DEFAULT NOW()
)

alerts (
  id SERIAL PRIMARY KEY,
  rule_id INTEGER REFERENCES alert_rules(id),
  endpoint_id INTEGER REFERENCES endpoints(id),
  message TEXT NOT NULL,
  severity TEXT DEFAULT 'warning', -- info|warning|critical
  acknowledged BOOLEAN DEFAULT false,
  acknowledged_by TEXT,
  acknowledged_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
)

-- Automation
runbooks (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  steps TEXT NOT NULL, -- JSON array of steps
  created_at TIMESTAMPTZ DEFAULT NOW()
)

automation_runs (
  id SERIAL PRIMARY KEY,
  runbook_id INTEGER REFERENCES runbooks(id),
  triggered_by TEXT, -- manual|schedule|alert
  endpoint_id INTEGER REFERENCES endpoints(id),
  status TEXT DEFAULT 'running', -- running|success|failed
  started_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
)

-- Auth
users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT DEFAULT 'technician', -- admin|technician|viewer
  created_at TIMESTAMPTZ DEFAULT NOW()
)

audit_log (
  id SERIAL PRIMARY KEY,
  user_id INTEGER,
  action TEXT NOT NULL,
  resource TEXT NOT NULL,
  resource_id INTEGER,
  details TEXT, -- JSON
  ip_address TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
)

schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TIMESTAMPTZ DEFAULT NOW()
)
```

### 1.4 WebSocket Hub (`server/ws/`)

**Hub Architecture:**
- One hub per server instance
- Agents and terminals register as "clients" in the hub
- Broadcasts to individual clients or groups (e.g., all agents for client X)
- Each connection runs in its own goroutine
- Heartbeat ping/pong every 30s to detect dead connections
- Dead connections cleaned up within 60s

**Message Types (agent ↔ server):**
```json
{ "type": "heartbeat", "agent_id": 42, "status": "online", "metrics": {...} }
{ "type": "check_result", "check_id": 7, "status": "ok", "output": "..." }
{ "type": "execute_script", "script_id": "uuid", "script": "powershell -Command ..." }
{ "type": "script_result", "script_id": "uuid", "exit_code": 0, "output": "..." }
{ "type": "file_transfer", "file_id": "uuid", "data": "base64...", "chunk": 1, "total": 5 }
{ "type": "update_push", "version": "1.2.0", "download_url": "/ws/agent/binary" }
```

**Terminal (interactive shell):**
- PTY allocated on agent side
- Terminal resize events forwarded
- ANSI escape codes preserved
- Connection authenticated via endpoint JWT

### 1.5 Services (`server/services/`)

**Agent Service:**
- Handles agent registration (first-run key → JWT issued)
- Manages agent heartbeat processing
- Tracks online/offline state
- Coordinates script execution requests

**Monitoring Service:**
- Schedules checks against endpoints
- Collects results from agents via WebSocket
- Stores metrics and check history
- Triggers alerting on threshold violations

**Alerting Service:**
- Evaluates alert rules on new check results
- Dispatches notifications (email, webhook, PagerDuty)
- Deduplicates repeated alerts (cooldown period)
- Tracks acknowledgment state

**Ticket Service:**
- Full ticket lifecycle (open → resolved → closed)
- SLA timer tracking
- Assignment and reassignment
- Comment threading

**Billing Service:**
- Generates invoice numbers (configurable format)
- Tracks payment status
- Calculates from time entries and contract values

---

## 2. Agent (`agent/`)

### 2.1 Overview

The agent is a **compiled Go binary** (~5–10 MB, depending on OS). It is:
- Embedded in the server binary via `go:embed`
- Streamed to endpoints on-demand via the WebSocket connection
- Self-updating (checks version on each heartbeat, downloads update if newer)

**Supported Platforms:**
- `windows/amd64` — `.exe` installer
- `linux/amd64` — static binary (no libc needed)
- `linux/arm64` — for ARM-based devices
- `darwin/amd64` — macOS Intel
- `darwin/arm64` — macOS Apple Silicon

### 2.2 Agent Communication Protocol

1. **Registration** (first run only):
   - Agent sends `{ "type": "register", "hostname": "...", "os": "...", "key": "..." }`
   - Server responds with JWT `{ "token": "...", "endpoint_id": 42 }`
   - Agent persists token to local config

2. **Heartbeat** (every 30s):
   - Agent sends `{ "type": "heartbeat", "status": "online", "metrics": { "cpu": 12.4, "memory": 67.2, ... } }`
   - Server responds `{ "type": "ack", "pending_scripts": [...] }`

3. **Script Execution** (triggered by server):
   - Server pushes `{ "type": "execute_script", "script_id": "uuid", "script": "..." }`
   - Agent executes locally, sends result `{ "type": "script_result", "script_id": "uuid", "exit_code": 0, "output": "..." }`

4. **File Transfer**:
   - Server initiates, agent receives in chunks
   - Used for config files, updates, remote tool delivery

5. **Auto-Update**:
   - Server sends `{ "type": "update_push", "version": "1.2.0", "sha256": "..." }`
   - Agent downloads binary from server, verifies checksum, replaces on next restart

### 2.3 Agent Deployment

**Windows:**
```powershell
# Single command installer (downloads and installs as Windows service)
powershell -Command "iwr https://server.example.com/api/agents/windows | iex"
```

**Linux/macOS:**
```bash
curl https://server.example.com/api/agents/install.sh | sh
```

The agent registers as a system service (Windows Service, systemd, launchd) for persistence.

---

## 3. Technician CLI (`cli/`)

A Go CLI tool for technicians who prefer the terminal over the web UI.

**Built with:** Cobra CLI framework

**Commands:**
```
msp-tools-cli login                    # Authenticate, store JWT
msp-tools-cli endpoint list            # List endpoints
msp-tools-cli endpoint run-script      # Run script on endpoint
msp-tools-cli ticket list               # List tickets
msp-tools-cli ticket create             # Create ticket
msp-tools-cli time log                  # Log time entry
msp-tools-cli check run                 # Run checks manually
msp-tools-cli alert list                # List active alerts
```

---

## 4. Web UI (`web/`)

**Stack:**
- React 18 + TypeScript
- Vite build tool
- React Router v6 (SPA routing)
- TanStack Query (data fetching/caching)
- Tailwind CSS (utility styling)
- shadcn/ui (component library)

**Pages:**
- Dashboard (overview metrics, alerts, tickets)
- Clients (list, detail, health)
- Endpoints (list, detail, terminal, scripts)
- Monitoring (checks, alerts, rules)
- PSA (tickets, time, KB, contracts, billing)
- Reports (health, SLA, technician)
- Settings (users, integrations)

**State Management:**
- Server state via TanStack Query (automatic refetch, background sync)
- Local UI state via React Context (sidebar, theme, etc.)
- WebSocket connection managed in a top-level provider for live updates

---

## 5. Monitoring Scripts (`monitoring/`)

Existing Python scripts are retained for ad-hoc checks and integration with external systems (cron, Ansible, etc.). They are **not** used by the Go agent internally.

```
monitoring/
├── check-disk.py    # Disk usage via SSH
├── check-ssl.py     # SSL certificate expiry
└── check-uptime.py  # Ping/port availability
```

These remain Python 3.8+ and follow Nagios exit code conventions.

---

## 6. API Reference Summary

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | Login with username/password → JWT |
| POST | `/api/auth/refresh` | Refresh access token |
| POST | `/api/auth/agent-register` | Agent first-run registration |

### Clients

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/clients` | List all clients |
| POST | `/api/clients` | Create client |
| GET | `/api/clients/:id` | Get client |
| PUT | `/api/clients/:id` | Update client |
| DELETE | `/api/clients/:id` | Delete client |
| GET | `/api/clients/:id/health` | Client health summary |

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/endpoints` | List endpoints (filter by client_id, status) |
| POST | `/api/endpoints` | Create endpoint record |
| GET | `/api/endpoints/:id` | Get endpoint |
| PUT | `/api/endpoints/:id` | Update endpoint |
| DELETE | `/api/endpoints/:id` | Delete endpoint |
| POST | `/api/endpoints/:id/run-script` | Execute script via agent |
| GET | `/api/endpoints/:id/metrics` | Get metrics history |
| GET | `/api/endpoints/:id/checks` | Get check results |

### Checks

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/checks` | List check definitions |
| POST | `/api/checks` | Create check definition |
| POST | `/api/checks/schedule` | Schedule check against endpoint(s) |
| GET | `/api/checks/history` | Get check history |

### Alerts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/alerts` | List alerts (filter by severity, acknowledged) |
| PUT | `/api/alerts/:id/acknowledge` | Acknowledge alert |

### Tickets

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tickets` | List tickets (filter by status, priority, assigned_to) |
| POST | `/api/tickets` | Create ticket |
| GET | `/api/tickets/:id` | Get ticket with comments |
| PUT | `/api/tickets/:id` | Update ticket |
| POST | `/api/tickets/:id/comments` | Add comment |
| PUT | `/api/tickets/:id/status` | Change ticket status |

### Time Entries

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/time-entries` | List time entries |
| POST | `/api/time-entries` | Log time entry |
| PUT | `/api/time-entries/:id` | Update time entry |

### Knowledge Base

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/kb` | List KB articles (search by title, category, tags) |
| POST | `/api/kb` | Create article |
| GET | `/api/kb/:id` | Get article |
| PUT | `/api/kb/:id` | Update article |

### Contracts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/contracts` | List contracts |
| POST | `/api/contracts` | Create contract |
| GET | `/api/contracts/:id` | Get contract |

### Invoices

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/invoices` | List invoices |
| POST | `/api/invoices` | Create invoice |
| GET | `/api/invoices/:id` | Get invoice |
| PUT | `/api/invoices/:id/status` | Update invoice status |

### Reports

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/reports/health/:client_id` | Client health report |
| GET | `/api/reports/sla` | SLA compliance report |
| GET | `/api/reports/technician` | Technician activity report |
| GET | `/api/reports/monthly/:client_id` | Monthly summary report |

---

## 7. Security Model

- **TLS required** for all HTTP and WebSocket connections in production
- **JWT access tokens** expire in 1 hour; refresh tokens expire in 7 days
- **Agent JWTs** are long-lived (30d) and scoped to a single endpoint
- **Role-based access control (RBAC):**
  - `admin` — full access including user management
  - `technician` — read/write on clients, endpoints, tickets, time; no billing
  - `viewer` — read-only on all resources
- **Audit logging** — all write operations logged with user, IP, timestamp, and details
- **Input validation** — struct tags + validator library on all API inputs
- **SQL injection prevention** — parameterised queries only (no string concatenation)
- **Script execution sandboxing** — agents run scripts with minimal privileges; PowerShell constrained language mode on Windows

---

## 8. Configuration

All configuration via `server/config/config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  tls:
    enabled: true
    cert_file: /etc/msp-tools/tls/server.crt
    key_file: /etc/msp-tools/tls/server.key

database:
  driver: sqlite  # sqlite | postgres
  dsn: ./data/msp-tools.db
  # For PostgreSQL:
  # dsn: postgres://user:pass@localhost:5432/msp-tools?sslmode=require

jwt:
  secret: "change-me-in-production"
  access_ttl: 1h
  refresh_ttl: 168h  # 7 days
  agent_ttl: 720h   # 30 days

agent:
  heartbeat_interval: 30s
  heartbeat_timeout: 90s
  embedded_binary_path: ./agent/

alerting:
  email:
    enabled: false
    smtp_host: smtp.example.com
    smtp_port: 587
    from: alerts@example.com
  webhook:
    enabled: true
    url: https://hooks.example.com/msp-tools

logging:
  level: info  # debug | info | warn | error
  format: json # json | text
  output: stdout # stdout | file
  file: ./logs/server.log
```

---

## 9. Directory Structure

```
msp-tools/
├── SPEC.md                      # This file
├── ARCHITECTURE.md              # System architecture (separate doc)
├── README.md
├── go.mod / go.sum              # Go module files
├── server/
│   ├── main.go                  # Entry point + graceful shutdown
│   ├── go.mod
│   ├── api/
│   │   ├── routes.go            # Route registration
│   │   ├── middleware/
│   │   │   ├── auth.go          # JWT validation
│   │   │   ├── logging.go       # Structured request logging
│   │   │   ├── ratelimit.go     # Rate limiting
│   │   │   └── cors.go          # CORS headers
│   │   ├── auth.go              # Auth handlers
│   │   ├── clients.go           # Client CRUD
│   │   ├── endpoints.go        # Endpoint CRUD + script execution
│   │   ├── monitoring.go        # Checks + alerts
│   │   ├── tickets.go           # Ticket CRUD + comments
│   │   ├── time.go              # Time entries
│   │   ├── kb.go                # Knowledge base
│   │   ├── contracts.go         # Contracts
│   │   ├── billing.go           # Invoices
│   │   └── reports.go           # Report generation
│   ├── db/
│   │   ├── db.go                # SQLite/PostgreSQL connection
│   │   ├── migrate.go           # Schema migrations
│   │   └── models/
│   │       ├── client.go
│   │       ├── endpoint.go
│   │       ├── ticket.go
│   │       ├── time_entry.go
│   │       ├── kb.go
│   │       ├── contract.go
│   │       ├── invoice.go
│   │       ├── check.go
│   │       ├── alert.go
│   │       ├── user.go
│   │       └── audit.go
│   ├── services/
│   │   ├── agent.go             # Agent registration + heartbeat
│   │   ├── monitoring.go        # Check scheduling
│   │   ├── alerting.go          # Alert evaluation + dispatch
│   │   ├── ticket.go            # Ticket workflow
│   │   └── billing.go           # Invoice generation
│   ├── ws/
│   │   ├── hub.go               # WebSocket connection hub
│   │   ├── agent.go             # Agent WS message handlers
│   │   └── terminal.go          # Interactive terminal WS
│   └── config/
│       └── config.go            # Configuration loader
├── agent/
│   ├── main.go                  # Agent entry point
│   ├── client/                  # Agent WS client
│   ├── checks/                  # Built-in check executors
│   ├── scripts/                 # Script runner
│   └── README.md                # Agent deployment guide
├── web/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── pages/
│   │   ├── components/
│   │   └── api/
│   └── tailwind.config.js
├── cli/
│   ├── main.go                  # CLI entry point
│   └── commands/                # Cobra command implementations
├── monitoring/                   # Existing Python scripts (unchanged)
│   ├── check-disk.py
│   ├── check-ssl.py
│   └── check-uptime.py
├── psa/                         # PSA module scripts (Python)
├── automation/                  # Runbook definitions (YAML)
├── data/                        # SQLite DB, uploaded files
├── logs/                        # Server logs
└── config/                      # Configuration files
```

---

## 10. Backlog / Future Work

### Near-term
- [ ] Agent auto-update (binary streaming from server)
- [ ] Alert rule builder UI
- [ ] Ticket email integration (receive replies as comments)
- [ ] Webhook alerting (generic + PagerDuty)
- [ ] Remote terminal (interactive shell over WebSocket)

### Medium-term
- [ ] PostgreSQL support with connection pooling
- [ ] Read replica support for reporting
- [ ] Time entry timer (start/stop from UI)
- [ ] SLA calculation and breach alerting
- [ ] Batch script execution across multiple endpoints
- [ ] Software inventory and patch management

### Long-term
- [ ] Multi-tenant / MSP white-label
- [ ] RMM + PSA unified dashboard
- [ ] Client portal (self-service ticket submission)
- [ ] API key auth for external integrations
- [ ] Backup verification checks
- [ ] Network discovery (scan subnet for new endpoints)
