# MSP Tools Suite — Specification

## Overview

A lightweight Python-based toolkit for Managed Service Providers (MSPs) to monitor client infrastructure, manage client records, and generate health reports.

**Python:** 3.8+  
**Data Storage:** SQLite (`data/`)  
**Logs:** `logs/` (rotated, timestamped)  
**Configs:** `config/` (JSON/YAML per client or globally)  
**Auth:** SSH key-based for remote checks

---

## 1. Monitoring Tools (`monitoring/`)

### 1.1 `check-disk.py` — Disk Space Monitoring
Checks disk usage on local or remote hosts via SSH.

**Features:**
- SSH to remote hosts (key-based auth)
- Check one or more mount points
- Configurable thresholds (warning/critical)
- Output: human-readable + JSON
- Log results to `logs/`

**Usage:**
```bash
./monitoring/check-disk.py --host server1.example.com --user admin --path / --warn 80 --crit 90
./monitoring/check-disk.py --host server1.example.com --path / --path /home --crit 95
```

**Exit codes:** 0=OK, 1=WARN, 2=CRIT, 3=UNKNOWN

---

### 1.2 `check-ssl.py` — SSL Certificate Expiry Check
Checks TLS/SSL certificate expiration on remote hosts/ports.

**Features:**
- Connect to any host:port (default 443)
- Alert N days before expiry (configurable)
- Check multiple hosts at once
- Output: human-readable + JSON
- Supports SNI for virtual hosts

**Usage:**
```bash
./monitoring/check-ssl.py --host example.com
./monitoring/check-ssl.py --host example.com --port 443 --days 30
./monitoring/check-ssl.py --hosts hosts.txt --days 14
```

**Exit codes:** 0=OK (cert valid > threshold), 1=WARN (expiring soon), 2=CRIT (expired/expiring very soon), 3=UNKNOWN

---

### 1.3 `check-uptime.py` — Ping/Port Monitoring
Ping hosts and check TCP port availability.

**Features:**
- ICMP ping check
- TCP port check (configurable port/timeout)
- Multiple hosts in one run
- Configurable retry count and interval
- Output: human-readable + JSON

**Usage:**
```bash
./monitoring/check-uptime.py --host 192.168.1.1
./monitoring/check-uptime.py --host example.com --port 22 --port 443
./monitoring/check-uptime.py --hosts hosts.txt --ping
```

**Exit codes:** 0=UP, 1=DEGRADED, 2=DOWN, 3=UNKNOWN

---

## 2. Client Management (`clients/`)

### 2.1 `db.py` — SQLite Client Database
Manages the MSP client registry.

**Features:**
- SQLite backend (`data/clients.db`)
- Add, update, delete, list clients
- Store: name, contact, hostname, IP, SSH port, notes, tags
- Search by name/tag/hostname
- Export to CSV/JSON

**Usage:**
```bash
./clients/db.py add --name "Acme Corp" --contact "it@acme.com" --host "server.acme.com" --ip "192.168.1.10" --port 22 --tag "production" --tag "windows"
./clients/db.py list
./clients/db.py list --tag "production"
./clients/db.py show --id 1
./clients/db.py update --id 1 --contact "newcontact@acme.com"
./clients/db.py delete --id 1
./clients/db.py export --format csv --output clients.csv
```

---

## 3. Reporting (`reports/`)

### 3.1 `client-health.py` — Client Health Reports
Aggregates monitoring data and generates a client health status report.

**Features:**
- Reads from client DB (`clients/db.py`)
- Runs disk/SSL/uptime checks per client
- Generates summary report (text + JSON)
- Per-client breakdown of issues
- Configurable thresholds
- Outputs to `reports/` with timestamps

**Usage:**
```bash
./reports/client-health.py
./reports/client-health.py --format json --output health-report.json
./reports/client-health.py --tag "production"
./reports/client-health.py --days 30  # include history
```

---

## Directory Structure

```
~/msp-tools/
├── SPEC.md
├── README.md
├── requirements.txt
├── monitoring/
│   ├── __init__.py
│   ├── check-disk.py
│   ├── check-ssl.py
│   └── check-uptime.py
├── clients/
│   ├── __init__.py
│   └── db.py
├── reports/
│   ├── __init__.py
│   └── client-health.py
├── data/           # SQLite DB, exported reports
├── logs/           # Timestamped run logs
└── config/         # JSON configs, host lists
```

---

## Future Features (Backlog)

### Monitoring
- `check-memory.py` — RAM/swap usage on remote hosts
- `check-cpu.py` — CPU load average
- `check-services.py` — systemd/service status via SSH
- `check-backups.py` — verify backup presence/freshness
- `check-processes.py` — count running processes
- Alerting: email/webhook integration

### Client Management
- Web UI (Flask/FastAPI)
- API for external integrations
- Ticket/integration tracking (ConnectWise, HaloPSA schema)
- Domain/SSL expiry tracking linked to clients

### Reporting
- Scheduled report generation (cron)
- HTML email reports
- Trend graphs (matplotlib integration)
- SLA tracking

### Automation
- `msp-run.py` — orchestrator to run all checks against a client
- Configurable check sets per client (not all checks apply to all clients)
- Ansible/Tower integration hooks

---

## Conventions

- All tools: `--help` shows full usage
- All tools: `--json` outputs JSON (machine-readable)
- All tools: exit codes follow Nagios plugin convention (0/1/2/3)
- Logging: timestamps in ISO 8601
- Config files: JSON, stored in `config/`
- SSH: key-based auth only, no password prompts
