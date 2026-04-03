# MSP Tools Platform — Architecture Document

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Database Architecture](#3-database-architecture)
4. [API Architecture](#4-api-architecture)
5. [Agent Communication Protocol](#5-agent-communication-protocol)
6. [WebSocket Architecture](#6-websocket-architecture)
7. [Security Architecture](#7-security-architecture)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Directory Structure Detail](#9-directory-structure-detail)

---

## 1. System Overview

### 1.1 Platform Purpose

The MSP Tools Platform is a self-hosted Remote Monitoring & Management (RMM) and Professional Services Automation (PSA) system designed for small-to-medium Managed Service Providers (MSPs). It provides complete infrastructure visibility, ticketing, time tracking, billing, and automation capabilities without any external cloud dependencies.

### 1.2 System Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                      Internet / WAN                          │
└───────────────────────────┬─────────────────────────────────┘
                            │ (optional, for remote agents)
┌───────────────────────────▼─────────────────────────────────┐
│                                                             │
│   ┌──────────────────────────────────────────────────────┐ │
│   │              DMZ / Perimeter Network                  │ │
│   │   (optional reverse proxy, TLS termination)           │ │
│   └──────────────────────────────────────────────────────┘ │
│                            │                                 │
│   ┌────────────────────────▼────────────────────────────────▼─┐
│   │                                                          │ │
│   │  ┌─────────────────┐  ┌─────────────────────────────┐  │ │
│   │  │   MSP Server    │  │      SQLite Database        │  │ │
│   │  │  (FastAPI + WS) │◄─┤   (/opt/msp-tools/data/)    │  │ │
│   │  │  Port 8443/8443  │  └─────────────────────────────┘  │ │
│   │  └────────┬────────┘                                     │ │
│   │           │                                              │ │
│   │  ┌────────▼────────┐  ┌─────────────────────────────┐  │ │
│   │  │   Web UI        │  │    File Storage             │  │ │
│   │  │   Port 8080    │  │  /opt/msp-tools/data/files/ │  │ │
│   │  └─────────────────┘  │  /opt/msp-tools/logs/       │  │ │
│   │                      └─────────────────────────────┘  │ │
│   │                                                          │ │
│   └──────────────────────────────────────────────────────────┘
│                            │
         LAN / VPN           │
┌────────┬────────┬──────────┴──────┬─────────────┬────────────┐
│        │        │                 │             │            │
│  ┌─────▼──┐ ┌───▼───┐       ┌────▼────┐ ┌──────▼─────┐ ┌───▼────┐
│  │ Linux  │ │Windows│       │ macOS   │ │  Network   │ │  VoIP  │
│  │ Agent  │ │ Agent │       │ Agent   │ │  Devices   │ │ Gateway│
│  │port 443│ │ port  │       │ port 443│ │  (SNMP)   │ │(SNMP)  │
│  └────────┘ └───────┘       └─────────┘ └────────────┘ └────────┘
```

### 1.3 Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| FastAPI Server | REST API, WebSocket handling, business logic, scheduling |
| SQLite Database | All persistent data (clients, endpoints, tickets, time, config) |
| File Storage | Uploaded attachments, generated PDFs, agent scripts, logs |
| Web UI | Browser-based admin interface (served by Flask/FastAPI) |
| Agent Service | Per-endpoint daemon: collects data, runs checks, reports to server |
| CLI Tools | Technician command-line operations |
| Customer Portal | End-customer ticket view/submission |

---

## 2. Technology Stack

### 2.1 Core Server

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| Runtime | Python | 3.9+ | Primary language |
| Web Framework | FastAPI | 0.109+ | REST API, WebSocket |
| ASGI Server | uvicorn | 0.27+ | ASGI server with workers |
| ORM | SQLAlchemy | 2.0+ | Database abstraction |
| Database | SQLite | 3.x | Data storage |
| Validation | Pydantic | 2.0+ | Data models, settings |
| Auth | python-jose | 3.3+ | JWT handling |
| Passwords | passlib + bcrypt | 1.7+ | Password hashing |
| Task Scheduler | APScheduler | 3.10+ | Cron-like scheduling |
| PDF Generation | reportlab | 4.0+ | Invoice/report PDFs |
| Logging | structlog | 24.0+ | Structured JSON logging |
| Config | PyYAML | 6.0+ | YAML configuration |

### 2.2 Agent Runtime

| OS | Language | Runtime |
|----|----------|---------|
| Linux | Python 3 | System Python3, requires 3.8+ |
| Windows | Python 3 or Go | Embedded Python or standalone Go binary |
| macOS | Python 3 | System Python3 |

### 2.3 Web UI

| Layer | Technology |
|-------|------------|
| Framework | FastAPI (serving Jinja2 templates) or Flask |
| Templates | Jinja2 HTML |
| CSS | Vanilla CSS (no framework dependency) or minimal Tailwind |
| JavaScript | Vanilla JS (no heavy framework) |
| Icons | Inline SVG or Font Awesome (self-hosted) |
| Charts | Chart.js (self-hosted) |

---

## 3. Database Architecture

### 3.1 Schema Overview (Entity Relationship)

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│    users     │       │      clients      │       │  endpoints   │
├──────────────┤       ├──────────────────┤       ├──────────────┤
│ id (PK)      │       │ id (PK)          │       │ id (PK)      │
│ username     │       │ name             │       │ client_id(FK)│
│ email        │       │ contact_*        │       │ hostname     │
│ password_hash│       │ health_score     │       │ os_*         │
│ role         │       │ tags             │       │ last_seen    │
│ is_active    │       └────────┬─────────┘       │ agent_status │
└──────────────┘                │                 └───────┬──────┘
       │                        │                         │
       │                        │ 1:N                     │ 1:N
       │                        ▼                         ▼
       │               ┌──────────────────┐       ┌──────────────┐
       │               │    contracts     │       │    checks    │
       │               ├──────────────────┤       ├──────────────┤
       │               │ id (PK)          │       │ id (PK)      │
       │               │ client_id (FK)   │       │ endpoint_id  │
       │               │ monthly_value    │       │ check_type   │
       │               │ end_date         │       │ interval_sec │
       │               └──────────────────┘       └──────┬───────┘
       │                                                   │
       │                        ┌─────────────────────────┘
       │                        │ 1:N
       │                        ▼
       │               ┌──────────────────┐       ┌──────────────┐
       │               │    alerts        │       │   assets     │
       │               ├──────────────────┤       ├──────────────┤
       │               │ id (PK)          │       │ id (PK)      │
       │               │ endpoint_id (FK) │       │ endpoint_id  │
       │               │ check_id (FK)    │       │ asset_type   │
       │               │ status           │       │ name         │
       │               └──────────────────┘       │ version      │
       │                                           └──────────────┘
       │
       │
┌──────▼──────────────────┐       ┌──────────────────┐
│        tickets          │       │    projects      │
├─────────────────────────┤       ├──────────────────┤
│ id (PK)                 │       │ id (PK)          │
│ ticket_number           │       │ client_id (FK)   │
│ client_id (FK)          │       │ name             │
│ endpoint_id (FK)        │       │ status           │
│ title                   │       │ start_date       │
│ status                  │       │ end_date         │
│ priority                │       └────────┬─────────┘
│ assigned_to (FK→users)  │                │ 1:N
│ sla_*                   │                ▼
│ billed_minutes          │       ┌──────────────────┐
└───────────┬─────────────┘       │  project_tasks   │
            │                     ├──────────────────┤
            │ 1:N                 │ id (PK)          │
            ▼                     │ project_id (FK)  │
┌───────────────────────────┐    │ title            │
│     ticket_comments       │    │ status           │
├───────────────────────────┤    │ assigned_to(FK) │
│ id (PK)                   │    └──────────────────┘
│ ticket_id (FK)            │
│ user_id (FK)              │    ┌──────────────────┐
│ content                   │    │  time_entries    │
│ is_internal               │    ├──────────────────┤
└───────────────────────────┘    │ id (PK)          │
                                 │ user_id (FK)     │
┌───────────────────────────┐    │ client_id (FK)   │
│     time_entries          │    │ ticket_id (FK)   │
├───────────────────────────┤    │ project_id (FK)  │
│ id (PK)                  │    │ start_time       │
│ user_id (FK)             │    │ end_time         │
│ client_id (FK)           │    │ billable         │
│ ticket_id (FK)           │    └──────────────────┘
│ description               │
│ start_time                │
│ duration_minutes          │    ┌──────────────────┐
└───────────────────────────┘    │    invoices      │
                                 ├──────────────────┤
┌───────────────────────────┐    │ id (PK)          │
│     kb_articles           │    │ invoice_number   │
├───────────────────────────┤    │ client_id (FK)   │
│ id (PK)                   │    │ status           │
│ title                     │    │ total            │
│ slug                      │    │ issue_date       │
│ content                   │    │ due_date         │
│ category                  │    └────────┬─────────┘
│ views                     │             │ 1:N
└───────────────────────────┘             ▼
                                 ┌──────────────────────┐
┌───────────────────────────┐    │  invoice_line_items  │
│        runbooks           │    ├──────────────────────┤
├───────────────────────────┤    │ id (PK)             │
│ id (PK)                   │    │ invoice_id (FK)     │
│ name                      │    │ description         │
│ trigger_type              │    │ quantity            │
│ steps (JSON)              │    │ unit_price          │
│ enabled                   │    │ total               │
└───────────────────────────┘    │ time_entry_id (FK)  │
                                 └──────────────────────┘

┌───────────────────────────┐
│     automation_events     │
├───────────────────────────┤
│ id (PK)                   │
│ event_type                │
│ source_type               │
│ source_id                 │
│ payload (JSON)            │
│ handled                   │
└───────────────────────────┘

┌───────────────────────────┐
│       audit_log           │
├───────────────────────────┤
│ id (PK)                   │
│ user_id (FK)              │
│ action                    │
│ entity_type               │
│ entity_id                 │
│ details (JSON)            │
│ ip_address                │
│ created_at                │
└───────────────────────────┘
```

### 3.2 Indexes

```sql
-- Performance indexes
CREATE INDEX idx_endpoints_client_id ON endpoints(client_id);
CREATE INDEX idx_endpoints_hostname ON endpoints(hostname);
CREATE INDEX idx_endpoints_last_seen ON endpoints(last_seen);
CREATE INDEX idx_checks_endpoint_id ON checks(endpoint_id);
CREATE INDEX idx_check_results_check_id ON check_results(check_id);
CREATE INDEX idx_check_results_executed_at ON check_results(executed_at);
CREATE INDEX idx_alerts_endpoint_id ON alerts(endpoint_id);
CREATE INDEX idx_alerts_status ON alerts(status);
CREATE INDEX idx_alerts_created_at ON alerts(created_at);
CREATE INDEX idx_tickets_client_id ON tickets(client_id);
CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_assigned_to ON tickets(assigned_to);
CREATE INDEX idx_tickets_sla_response_due ON tickets(sla_response_due);
CREATE INDEX idx_tickets_sla_resolution_due ON tickets(sla_resolution_due);
CREATE INDEX idx_ticket_comments_ticket_id ON ticket_comments(ticket_id);
CREATE INDEX idx_time_entries_user_id ON time_entries(user_id);
CREATE INDEX idx_time_entries_client_id ON time_entries(client_id);
CREATE INDEX idx_time_entries_ticket_id ON time_entries(ticket_id);
CREATE INDEX idx_time_entries_billed ON time_entries(billed);
CREATE INDEX idx_invoice_line_items_invoice_id ON invoice_line_items(invoice_id);
CREATE INDEX idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX idx_audit_log_created_at ON audit_log(created_at);
```

---

## 4. API Architecture

### 4.1 API Versioning Strategy

- Base path: `/api/v1/`
- Version in URL path (not headers) for simplicity
- Breaking changes increment version (v2, v3)
- Old versions supported for minimum 6 months after new release

### 4.2 Endpoint Summary

| Category | Endpoints | Methods |
|----------|-----------|---------|
| Auth | `/auth/login`, `/auth/logout`, `/auth/refresh` | POST |
| Clients | `/clients`, `/clients/{id}`, `/clients/{id}/endpoints` | GET, POST, PUT, DELETE |
| Endpoints | `/endpoints`, `/endpoints/{id}`, `/endpoints/{id}/checks`, `/endpoints/{id}/assets` | GET, POST, PUT, DELETE |
| Checks | `/checks`, `/checks/{id}`, `/checks/{id}/results`, `/checks/{id}/run` | GET, POST, PUT, DELETE |
| Alerts | `/alerts`, `/alerts/{id}`, `/alerts/{id}/acknowledge` | GET, POST, PUT |
| Tickets | `/tickets`, `/tickets/{id}`, `/tickets/{id}/comments`, `/tickets/{id}/attachments` | GET, POST, PUT, DELETE |
| Time | `/time-entries`, `/time-entries/{id}`, `/time-entries/timer/*` | GET, POST, PUT, DELETE |
| KB | `/kb`, `/kb/{id}`, `/kb/{slug}` | GET, POST, PUT, DELETE |
| Projects | `/projects`, `/projects/{id}`, `/projects/{id}/tasks` | GET, POST, PUT, DELETE |
| Contracts | `/contracts`, `/contracts/{id}` | GET, POST, PUT, DELETE |
| Invoices | `/invoices`, `/invoices/{id}`, `/invoices/{id}/pdf` | GET, POST, PUT |
| Runbooks | `/runbooks`, `/runbooks/{id}`, `/runbooks/{id}/execute` | GET, POST, PUT, DELETE |
| Reports | `/reports/health`, `/reports/sla`, `/reports/technician`, `/reports/monthly` | GET, POST |
| Agents | `/agents/register`, `/agents/{id}/heartbeat`, `/agents/{id}/results`, `/agents/{id}/command` | GET, POST |
| Users | `/users`, `/users/{id}`, `/users/{id}/password` | GET, POST, PUT, DELETE |

### 4.3 Request/Response Patterns

**Create (POST /api/v1/clients):**
```json
// Request
{
  "name": "Acme Corporation",
  "contact_name": "John Smith",
  "contact_email": "john@acme.com",
  "contact_phone": "+1-555-0100",
  "tags": ["enterprise", "priority"]
}

// Response (201 Created)
{
  "data": {
    "id": 1,
    "name": "Acme Corporation",
    "contact_name": "John Smith",
    "contact_email": "john@acme.com",
    "contact_phone": "+1-555-0100",
    "tags": ["enterprise", "priority"],
    "health_score": null,
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T10:30:00Z"
  }
}
```

**Update (PUT /api/v1/clients/{id}):**
```json
// Request
{
  "contact_email": "newcontact@acme.com"
}

// Response (200 OK)
{
  "data": { /* full updated object */ }
}
```

**List with Filter (GET /api/v1/tickets?status=open&priority=high&assigned_to=3):**
```json
// Response (200 OK)
{
  "data": [
    { /* ticket 1 */ },
    { /* ticket 2 */ }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_items": 47,
    "total_pages": 3,
    "has_next": true,
    "has_prev": false
  }
}
```

### 4.4 API Rate Limiting

- Default: 100 requests/minute per user
- Burst: 20 requests/second
- Agent endpoints: 1000 requests/minute per agent
- Return `429 Too Many Requests` when exceeded with `Retry-After` header

---

## 5. Agent Communication Protocol

### 5.1 Connection Lifecycle

```
Agent                              Server
  │                                    │
  │────────── TCP TLS Connect ────────▶│
  │                                    │
  │◀──── Server TLS Certificate ──────│
  │                                    │
  │────── Agent Hello + TLS Client ───▶│
  │     Certificate                    │
  │                                    │
  │◀──── Server Challenge ─────────────│
  │                                    │
  │────── Challenge Response ──────────▶│
  │     (signed with agent key)        │
  │                                    │
  │◀──── Access Token Granted ─────────│
  │     (JWT, short-lived)             │
  │                                    │
  │======= WebSocket Channel =========▶│
  │     (bidirectional JSON messages)   │
  │                                    │
  │  ... bidirectional messages ...    │
  │                                    │
  │◀──── Server: close / reconnect ────│
  │     (normal shutdown or error)     │
  │                                    │
  │======= Connection Closed =========│
```

### 5.2 WebSocket Message Protocol

All messages are JSON with a common envelope:

```json
{
  "id": "msg-uuid-001",
  "type": "message_type",
  "timestamp": "2024-01-15T10:30:00.123Z",
  "payload": { ... }
}
```

**Type Catalog:**

| Direction | Type | Description |
|-----------|------|-------------|
| S→A | `check_config` | Push new/modified check configuration to agent |
| S→A | `script_execute` | Request agent run a specific script |
| S→A | `file_push` | Push a file/script content to agent |
| S→A | `agent_update` | Request agent self-update |
| S→A | `agent_config_update` | Update agent settings |
| S→A | `ping` | Server heartbeat request |
| A→S | `check_result` | Agent reports check execution result |
| A→S | `script_output` | Agent returns script execution output |
| A→S | `heartbeat` | Agent status report (CPU, RAM, disk, online) |
| A→S | `log_push` | Agent sends collected logs |
| A→S | `pong` | Agent response to ping |
| A→S | `registration` | Initial agent registration (before TLS) |
| S→A | `ack` | Message acknowledgment |
| S→A | `nack` | Message negative acknowledgment |

### 5.3 Agent Registration Flow

```
1. Agent generates RSA-2048 keypair locally (first run only)
2. Agent sends POST /api/v1/agents/register:
   {
     "hostname": "server01.acme.com",
     "os": "linux",
     "os_version": "Ubuntu 22.04",
     "agent_version": "1.0.0",
     "public_key": "-----BEGIN PUBLIC KEY-----...",
     "client_id": "acme-001",
     "hardware_uuid": "..."
   }
3. Server validates client_id exists
4. Server generates agent certificate signed by server CA
5. Server stores agent record
6. Server returns:
   {
     "agent_id": "agent-uuid-001",
     "certificate": "-----BEGIN CERTIFICATE-----...",
     "ca_certificate": "-----BEGIN CERTIFICATE-----...",
     "server_ws_url": "wss://msp-server:8443/ws/agents/agent-uuid-001",
     "heartbeat_interval": 60
   }
7. Agent stores certificate locally
8. Agent connects to WebSocket using certificate
```

### 5.4 Heartbeat Specification

- Interval: 60 seconds (configurable, min 30, max 300)
- Payload:
```json
{
  "type": "heartbeat",
  "id": "hb-001",
  "timestamp": "2024-01-15T10:30:00Z",
  "payload": {
    "status": "online",
    "cpu_percent": 12.5,
    "memory_percent": 34.2,
    "disk_percent": 67.8,
    "uptime_seconds": 864000,
    "agent_version": "1.0.0",
    "boot_time": "2024-01-08T00:00:00Z",
    "interfaces": [
      {"name": "eth0", "ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:ff"}
    ],
    "check_status": {
      "total": 5,
      "ok": 4,
      "warn": 1,
      "crit": 0
    }
  }
}
```
- Server marks agent offline if no heartbeat for 5 minutes (configurable)
- Server generates `endpoint_offline` event after 3 missed heartbeats

---

## 6. WebSocket Architecture

### 6.1 WebSocket Endpoints

| Endpoint | Purpose | Auth |
|----------|---------|------|
| `/ws/agents/{agent_id}` | Agent bidirectional channel | TLS client cert |
| `/ws/ui` | Web UI real-time updates | JWT Bearer |

### 6.2 UI WebSocket Protocol

```json
// Client → Server (subscribe)
{"type": "subscribe", "channels": ["alerts", "tickets", "endpoints"]}

// Server → Client (push notification)
{"type": "notification", "channel": "alerts", "data": {"id": 123, "title": "Disk full on server01", "severity": "critical"}}

// Channels available:
alerts     — New/modified alerts
tickets    — Ticket changes (new, status change, comment)
endpoints  — Endpoint online/offline/status changes
checks     — Check results (optional, for real-time monitoring UI)
```

### 6.3 Connection Management

- Max concurrent WebSocket connections: 1000 (configurable)
- Per-user UI connections: 3 max
- Heartbeat ping/pong every 30 seconds
- Auto-reconnect with exponential backoff on client side
- Connection stored in Redis-like dict for message broadcasting (in-process for single-server, Redis for multi-worker)

---

## 7. Security Architecture

### 7.1 Authentication Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                        Request Flow                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. TLS Termination                                            │
│     └── Server certificate (self-signed or Let's Encrypt)       │
│                                                                 │
│  2. API Key / JWT                                               │
│     ├── Web UI: JWT Bearer token (15-min access + 7-day refresh)│
│     ├── CLI: JWT Bearer token (same as API)                    │
│     ├── Agents: TLS client certificate                         │
│     └── Portal: Email + bcrypt password                        │
│                                                                 │
│  3. Authorization (RBAC)                                        │
│     ├── admin: Full CRUD on all resources                      │
│     ├── technician: CRUD on managed entities, read all         │
│     ├── viewer: Read-only access                               │
│     └── customer: Own tickets + KB articles                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 Password Security

```python
# Password hashing with bcrypt
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# Hash password
hash = pwd_context.hash("user_password")

# Verify password
pwd_context.verify("user_password", hash)  # True/False
```

### 7.3 JWT Token Structure

```json
// Access Token Payload
{
  "sub": "1",                    // user_id
  "username": "admin",
  "role": "admin",
  "type": "access",
  "exp": 1705321800,             // Expiry timestamp
  "iat": 1705320900              // Issued at
}

// Refresh Token Payload
{
  "sub": "1",
  "type": "refresh",
  "exp": 1705925700,             // 7 days
  "iat": 1705320900
}
```

### 7.4 Agent Certificate Chain

```
┌─────────────────────────────────────────┐
│       Root CA (self-signed)             │
│  CN=MSP Tools CA                        │
│  Stored on server only                  │
└─────────────────┬───────────────────────┘
                  │ Signs
                  ▼
┌─────────────────────────────────────────┐
│   Server Certificate                    │
│  CN=msp-server.internal                 │
│  Presented to agents + clients          │
└─────────────────┬───────────────────────┘
                  │ Signs (on agent registration)
                  ▼
┌─────────────────────────────────────────┐
│   Agent Certificate                     │
│  CN=agent-uuid-001                      │
│  Each agent has unique cert             │
│  Stored in agent config directory       │
└─────────────────────────────────────────┘
```

### 7.5 Audit Logging Events

| Event | Logged Details |
|-------|---------------|
| `login` | user_id, ip, user_agent, success/failure |
| `logout` | user_id |
| `client_create` | user_id, client_id, client_name |
| `client_update` | user_id, client_id, changed_fields |
| `client_delete` | user_id, client_id |
| `endpoint_register` | agent_id, hostname, client_id |
| `endpoint_offline` | agent_id, last_seen |
| `ticket_create` | user_id, ticket_id, client_id |
| `ticket_status_change` | user_id, ticket_id, old_status, new_status |
| `ticket_assign` | user_id, ticket_id, assigned_to |
| `invoice_create` | user_id, invoice_id, client_id, total |
| `invoice_paid` | user_id, invoice_id, amount |
| `runbook_execute` | user_id, runbook_id, endpoint_id |
| `user_create` | admin_user_id, new_user_id, role |
| `user_delete` | admin_user_id, deleted_user_id |

### 7.6 Network Security

- Server binds to localhost by default; expose via reverse proxy for remote agents
- Agents connect outbound only (port 8443) — no inbound ports needed on endpoints
- Firewall rules: allow 8443 outbound from agent network to server
- Optional: VPN between agent network and server for additional isolation
- All inter-service communication on localhost only

---

## 8. Deployment Architecture

### 8.1 Single-Server Deployment

```
┌─────────────────────────────────────────────────┐
│              Ubuntu 22.04 LTS (VM)               │
│                   2 vCPU, 4GB RAM                │
│                                                 │
│  ┌─────────────────────────────────────────────┐ │
│  │ systemd: msp-server.service                 │ │
│  │   └── uvicorn + FastAPI                     │ │
│  │       - REST API: 0.0.0.0:8443             │ │
│  │       - WebSocket: 0.0.0.0:8443 (ws)      │ │
│  │       - Web UI: 0.0.0.0:8080 (optional)   │ │
│  └─────────────────────────────────────────────┘ │
│                                                 │
│  ┌─────────────────────────────────────────────┐ │
│  │ /opt/msp-tools/                             │ │
│  │   ├── server/ (application code)           │ │
│  │   ├── data/ (SQLite DB + uploads)           │ │
│  │   ├── logs/ (application logs)              │ │
│  │   └── config/ (YAML configs)               │ │
│  └─────────────────────────────────────────────┘ │
│                                                 │
│  ┌─────────────────────────────────────────────┐ │
│  │ Cron: Schedule reports, cleanup jobs        │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### 8.2 Production Deployment (Multi-Worker)

```
┌──────────────────────────────────────────────────────────────┐
│                    Load Balancer (nginx/haproxy)              │
│                    TLS termination, port 443                  │
└────────────────────────────┬─────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  Worker 1     │    │  Worker 2     │    │  Worker 3     │
│  uvicorn      │    │  uvicorn      │    │  uvicorn      │
│  Gunicorn     │    │  Gunicorn     │    │  Gunicorn     │
│  (prefork 4)  │    │  (prefork 4)  │    │  (prefork 4)  │
└───────┬───────┘    └───────┬───────┘    └───────┬───────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │        Redis (or SQLite)       │
              │   Shared state, WS sessions   │
              └──────────────────────────────┘
```

### 8.3 Installation Steps

```bash
# 1. Clone / extract to /opt/msp-tools
git clone https://github.com/your-repo/msp-tools.git /opt/msp-tools

# 2. Create virtual environment
python3 -m venv /opt/msp-tools/venv
source /opt/msp-tools/venv/bin/activate
pip install -r /opt/msp-tools/requirements.txt

# 3. Initialize database
python /opt/msp-tools/scripts/init-db.py

# 4. Generate TLS certificates
/opt/msp-tools/scripts/generate-certs.sh

# 5. Configure
cp /opt/msp-tools/config/defaults.yaml /opt/msp-tools/config/server.yaml
# Edit server.yaml with your settings

# 6. Create systemd service
cp /opt/msp-tools/scripts/msp-server.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable msp-server
systemctl start msp-server

# 7. Install agents on endpoints
# Run installer script on each endpoint (see Agent Installation section)
```

---

## 9. Directory Structure Detail

### 9.1 Server Module (`server/`)

```
server/
├── __init__.py                    # Package init, exports main app
├── main.py                        # FastAPI app creation, CORS, lifespan
├── api/
│   ├── __init__.py
│   ├── deps.py                    # Dependency injection (get_db, get_current_user)
│   ├── v1/
│   │   ├── __init__.py            # APIRouter aggregation
│   │   ├── auth.py               # /auth endpoints
│   │   ├── clients.py            # /clients endpoints
│   │   ├── endpoints.py          # /endpoints endpoints
│   │   ├── monitoring.py        # /checks, /alerts
│   │   ├── tickets.py           # /tickets, /ticket-comments
│   │   ├── time_entries.py      # /time-entries
│   │   ├── knowledge_base.py    # /kb
│   │   ├── projects.py          # /projects, /project-tasks
│   │   ├── contracts.py         # /contracts
│   │   ├── invoices.py         # /invoices, /invoice-items
│   │   ├── automation.py       # /runbooks, /events
│   │   ├── reports.py          # /reports/*
│   │   ├── agents.py           # /agents/*
│   │   └── users.py            # /users/*
│   └── errors.py               # Custom exception handlers
├── db/
│   ├── __init__.py
│   ├── database.py             # create_engine, get_session, init_db
│   ├── base.py                 # declarative_base
│   └── migrations/            # Alembic migrations
├── models/
│   ├── __init__.py            # Re-export all models
│   ├── user.py
│   ├── client.py
│   ├── endpoint.py
│   ├── asset.py
│   ├── check.py
│   ├── alert.py
│   ├── ticket.py
│   ├── time_entry.py
│   ├── kb_article.py
│   ├── project.py
│   ├── contract.py
│   ├── invoice.py
│   ├── runbook.py
│   └── audit_log.py
├── core/
│   ├── __init__.py
│   ├── config.py              # Settings class (from YAML + env)
│   ├── security.py           # JWT, password, certificate utils
│   ├── logging.py             # structlog setup
│   └── events.py             # Internal event bus (simple pub/sub)
├── ws/
│   ├── __init__.py
│   ├── manager.py            # WebSocket connection manager
│   ├── agents.py             # Agent WS handler
│   └── ui.py                 # UI WS handler (optional)
├── services/
│   ├── __init__.py
│   ├── monitoring.py         # Check execution orchestration
│   ├── alerting.py           # Alert creation, routing, email
│   ├── ticketing.py          # Ticket business logic
│   ├── billing.py            # Invoice generation logic
│   ├── automation.py         # Runbook executor
│   ├── health_score.py       # Health score calculation
│   ├── agent_comm.py         # Agent push/pull management
│   └── reports.py            # Report generation logic
└── tasks/
    ├── __init__.py
    └── scheduler.py          # APScheduler job definitions
```

### 9.2 Agent Module (`agents/`)

```
agents/
├── common/
│   ├── __init__.py
│   ├── config.py             # AgentConfiguration dataclass
│   ├── transport.py          # TLS/HTTP/WebSocket client
│   ├── checks.py            # Built-in check implementations
│   ├── crypto.py            # Certificate management
│   └── installer.py         # Cross-platform installer logic
├── linux/
│   ├── __init__.py
│   ├── agent.py             # Linux agent entry point
│   ├── requirements.txt
│   └── scripts/
│       ├── check-disk.sh
│       ├── check-memory.sh
│       ├── check-cpu.sh
│       ├── check-services.sh
│       ├── check-uptime.sh
│       └── check-load.sh
├── windows/
│   ├── __init__.py
│   ├── agent.py             # Windows agent entry point
│   ├── requirements.txt
│   ├── service.py           # Windows Service wrapper
│   └── scripts/
│       ├── check-disk.ps1
│       ├── check-memory.ps1
│       ├── check-cpu.ps1
│       └── check-services.ps1
└── macos/
    ├── __init__.py
    ├── agent.py             # macOS agent entry point
    └── requirements.txt
```

### 9.3 Data Directory (`data/`)

```
data/
├── msp.db                    # Main SQLite database
├── msp.db-wal                # SQLite WAL file
├── msp.db-shm               # SQLite shared memory
├── backups/                 # Automatic DB backups
│   ├── msp-2024-01-15.db
│   └── msp-2024-01-14.db
├── files/                   # Uploaded file storage
│   ├── attachments/         # Ticket attachment files
│   ├── agent-scripts/       # Custom agent scripts
│   └── certs/              # Generated certificates
├── reports/                 # Generated reports (CSV, PDF)
│   ├── health-2024-01-15.pdf
│   └── sla-2024-01.pdf
└── agent-pkg/              # Agent installer packages
    ├── linux-amd64.tar.gz
    ├── windows-amd64.zip
    └── macos-universal.tar.gz
```

---

## 10. Health Score Calculation

### 10.1 Formula

```
Client Health Score = 100 - (penalties)

Penalties:
  +10 per open CRITICAL alert
  +5 per open HIGH alert
  +2 per open MEDIUM alert
  +1 per open LOW alert
  +5 per open ticket > 7 days
  +5 per SLA breach (response)
  +10 per SLA breach (resolution)
  +10 per offline endpoint
  +5 per failed backup (last 24h)
  +3 per endpoint with disk > 90%
  +2 per endpoint with disk > 80%
```

### 10.2 Score Ranges

| Score | Status | Color |
|-------|--------|-------|
| 90-100 | Excellent | Green |
| 70-89 | Good | Light Green |
| 50-69 | Fair | Yellow |
| 25-49 | Poor | Orange |
| 0-24 | Critical | Red |

---

## 11. Monitoring Check Reference

### 11.1 Check Types

| Type | Description | Thresholds |
|------|-------------|-----------|
| `disk` | Disk space usage % | warn: 80, crit: 90 |
| `memory` | RAM usage % | warn: 80, crit: 90 |
| `cpu` | CPU usage % | warn: 80, crit: 95 |
| `load` | Load average vs CPU cores | warn: 0.75, crit: 1.0 |
| `uptime` | Ping/port check | timeout: 5s, retries: 3 |
| `ssl` | SSL certificate expiry | warn: 30 days, crit: 7 days |
| `service` | Service running status | (list of service names) |
| `process` | Process count/ram | (process name, warn/crit counts) |
| `backup` | Backup file existence/freshness | max_age_hours: 25 |
| `temperature` | Hardware temp (where available) | warn: 70°C, crit: 85°C |
| `windows_update` | Pending updates count | warn: 10, crit: 30 |
| `antivirus` | AV definition status | (days since update) |

### 11.2 Check Result Format

```json
{
  "check_id": 123,
  "status": "WARN",
  "value": 82.5,
  "output": "Disk usage on / is 82.5% (78.2G / 94.9G used)",
  "details": {
    "mount_point": "/",
    "total_gb": 94.9,
    "used_gb": 78.2,
    "free_gb": 16.7
  },
  "executed_at": "2024-01-15T10:30:00Z",
  "execution_ms": 145
}
```

---

## 12. SLA Configuration

### 12.1 SLA Tiers

| Tier | Response Time | Resolution Time | Hours |
|------|--------------|-----------------|-------|
| Critical | 15 minutes | 4 hours | 24x7 |
| High | 1 hour | 8 hours | Business |
| Medium | 4 hours | 24 hours | Business |
| Low | 8 hours | 72 hours | Business |

### 12.2 Business Hours

Configurable per-client:
```yaml
business_hours:
  timezone: "America/New_York"
  schedule:
    - day: monday
      start: "09:00"
      end: "17:00"
    - day: tuesday
      start: "09:00"
      end: "17:00"
    # ... etc
```

SLA clock stops outside business hours unless 24x7 tier.

---

*Document Version: 1.0*  
*Last Updated: 2024-01-15*
