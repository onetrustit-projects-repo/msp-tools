#!/usr/bin/env python3
"""
reports/client-health.py — Generate client health status reports.

Aggregates monitoring results from the client database and runs
disk/SSL/uptime checks per client to produce a health summary.

Outputs:
    - Human-readable text report to stdout (and optionally to a file)
    - JSON report for machine consumption
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import logging
from pathlib import Path

# Add parent dirs to path so we can import monitoring tools
sys.path.insert(0, str(Path(__file__).parent.parent / "monitoring"))
sys.path.insert(0, str(Path(__file__).parent.parent / "clients"))

DATA_DIR = Path(__file__).parent.parent / "data"
REPORTS_DIR = DATA_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging():
    log_file = LOG_DIR / f"client-health-{datetime.date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a client health status report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s --format json --output health.json
  %(prog)s --tag production
  %(prog)s --disk-warn 70 --disk-crit 85 --ssl-days 14
  %(prog)s --skip-disk --skip-ssl --skip-uptime
        """,
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write report to file (in addition to stdout)",
    )
    parser.add_argument(
        "--tag",
        action="append",
        dest="tags",
        help="Only check clients with these tag(s)",
    )
    parser.add_argument(
        "--disk-warn", type=int, default=80, help="Disk warning %% (default: 80)"
    )
    parser.add_argument(
        "--disk-crit", type=int, default=90, help="Disk critical %% (default: 90)"
    )
    parser.add_argument(
        "--ssl-days", type=int, default=30, help="SSL warning days (default: 30)"
    )
    parser.add_argument(
        "--ssh-key",
        type=Path,
        default=Path(os.path.expanduser("~/.ssh/id_rsa")),
        help="SSH private key for remote checks",
    )
    parser.add_argument(
        "--skip-disk", action="store_true", help="Skip disk checks"
    )
    parser.add_argument(
        "--skip-ssl", action="store_true", help="Skip SSL checks"
    )
    parser.add_argument(
        "--skip-uptime", action="store_true", help="Skip uptime/ping checks"
    )
    parser.add_argument(
        "--timeout", type=int, default=10, help="SSH/connection timeout (default: 10s)"
    )
    return parser.parse_args()


def get_clients(tags=None):
    """Load clients from SQLite DB, optionally filtered by tags."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "clients"))
    try:
        import sqlite3
    except ImportError:
        logging.error("sqlite3 not available")
        return []

    db_path = DATA_DIR / "clients.db"
    if not db_path.exists():
        logging.warning(f"Client DB not found at {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM clients WHERE 1=1"
    params = []
    if tags:
        tag_conditions = " OR ".join(["tags LIKE ?" for _ in tags])
        query += f" AND ({tag_conditions})"
        params.extend([f"%{t}%" for t in tags])

    query += " ORDER BY name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_check(script_name, script_args, timeout=60):
    """Run a monitoring script and capture its output/code."""
    script_path = Path(__file__).parent.parent / "monitoring" / script_name
    if not script_path.exists():
        return {"error": f"Script not found: {script_name}"}

    try:
        result = subprocess.run(
            ["python3", str(script_path)] + script_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Check timed out"}
    except Exception as e:
        return {"error": str(e)}


def check_client_disk(client, warn, crit, key_path, timeout):
    """Run disk check for a client."""
    if not client.get("host") or not client.get("ip"):
        return None

    args = [
        "--host", client["host"],
        "--user", "root",
        "--key", str(key_path),
        "--path", "/",
        "--warn", str(warn),
        "--crit", str(crit),
        "--timeout", str(timeout),
        "--json",
    ]
    result = run_check("check-disk.py", args, timeout=timeout + 10)
    if "error" in result:
        return {"status": "UNKNOWN", "code": 3, "error": result["error"]}
    try:
        data = json.loads(result["stdout"])
        return data
    except json.JSONDecodeError:
        return {"status": "UNKNOWN", "code": 3, "error": result.get("stderr", "JSON parse error")}


def check_client_ssl(client, days, timeout):
    """Run SSL check for a client."""
    if not client.get("host"):
        return None

    port = client.get("port", 443)
    args = [
        "--host", client["host"],
        "--port", str(port),
        "--days", str(days),
        "--timeout", str(timeout),
        "--json",
    ]
    result = run_check("check-ssl.py", args, timeout=timeout + 10)
    if "error" in result:
        return {"status": "UNKNOWN", "code": 3, "error": result["error"]}
    try:
        data = json.loads(result["stdout"])
        return data
    except json.JSONDecodeError:
        return {"status": "UNKNOWN", "code": 3, "error": result.get("stderr", "JSON parse error")}


def check_client_uptime(client, timeout):
    """Run uptime/ping check for a client."""
    if not client.get("ip"):
        return None

    args = [
        "--host", client["ip"],
        "--ping",
        "--timeout", str(timeout),
        "--json",
    ]
    result = run_check("check-uptime.py", args, timeout=timeout + 10)
    if "error" in result:
        return {"status": "UNKNOWN", "code": 3, "error": result["error"]}
    try:
        data = json.loads(result["stdout"])
        return data
    except json.JSONDecodeError:
        return {"status": "UNKNOWN", "code": 3, "error": result.get("stderr", "JSON parse error")}


def generate_report(args, logger):
    """Build the full report."""
    clients = get_clients(tags=args.tags)
    timestamp = datetime.datetime.now().isoformat()

    report = {
        "generated_at": timestamp,
        "thresholds": {
            "disk_warn": args.disk_warn,
            "disk_crit": args.disk_crit,
            "ssl_days": args.ssl_days,
        },
        "total_clients": len(clients),
        "clients": [],
    }

    overall_worst = 0
    summary = {"ok": 0, "warn": 0, "crit": 0, "unknown": 0, "skipped": 0}

    for client in clients:
        entry = {
            "id": client["id"],
            "name": client["name"],
            "host": client.get("host"),
            "ip": client.get("ip"),
            "contact": client.get("contact"),
            "tags": client.get("tags"),
            "checks": {},
        }
        client_worst = 0

        # Disk check
        if args.skip_disk:
            entry["checks"]["disk"] = {"status": "SKIPPED", "code": -1}
            summary["skipped"] += 1
        else:
            logger.info(f"Disk check: {client['name']}")
            disk_result = check_client_disk(
                client, args.disk_warn, args.disk_crit, args.ssh_key, args.timeout
            )
            if disk_result is None:
                entry["checks"]["disk"] = {"status": "SKIPPED", "code": -1}
                summary["skipped"] += 1
            else:
                entry["checks"]["disk"] = disk_result
                code = disk_result.get("worst_exit_code", 0)
                if code > client_worst:
                    client_worst = code

        # SSL check
        if args.skip_ssl:
            entry["checks"]["ssl"] = {"status": "SKIPPED", "code": -1}
            summary["skipped"] += 1
        else:
            logger.info(f"SSL check: {client['name']}")
            ssl_result = check_client_ssl(client, args.ssl_days, args.timeout)
            if ssl_result is None:
                entry["checks"]["ssl"] = {"status": "SKIPPED", "code": -1}
                summary["skipped"] += 1
            else:
                entry["checks"]["ssl"] = ssl_result
                code = ssl_result.get("worst_exit_code", 0)
                if code > client_worst:
                    client_worst = code

        # Uptime check
        if args.skip_uptime:
            entry["checks"]["uptime"] = {"status": "SKIPPED", "code": -1}
            summary["skipped"] += 1
        else:
            logger.info(f"Uptime check: {client['name']}")
            uptime_result = check_client_uptime(client, args.timeout)
            if uptime_result is None:
                entry["checks"]["uptime"] = {"status": "SKIPPED", "code": -1}
                summary["skipped"] += 1
            else:
                entry["checks"]["uptime"] = uptime_result
                code = uptime_result.get("worst_exit_code", 0)
                if code > client_worst:
                    client_worst = code

        entry["worst_exit_code"] = client_worst
        if client_worst > overall_worst:
            overall_worst = client_worst

        # Count in summary
        status_map = {0: "ok", 1: "warn", 2: "crit", 3: "unknown"}
        summary[status_map.get(client_worst, "unknown")] += 1

        report["clients"].append(entry)

    report["overall_worst_exit_code"] = overall_worst
    report["summary"] = summary
    return report


def format_text_report(report):
    """Render report as human-readable text."""
    lines = []
    ts = report["generated_at"]
    total = report["total_clients"]
    s = report["summary"]

    lines.append("=" * 70)
    lines.append(f"  MSP Client Health Report — {ts}")
    lines.append("=" * 70)
    lines.append(
        f"  Clients: {total} total | "
        f"✅ {s['ok']} OK | ⚠️ {s['warn']} WARN | 🔴 {s['crit']} CRIT | ❓ {s['unknown']} UNKNOWN | ⏭️ {s['skipped']} SKIPPED"
    )
    lines.append("-" * 70)
    lines.append(
        f"  Thresholds — Disk: warn={report['thresholds']['disk_warn']}%, "
        f"crit={report['thresholds']['disk_crit']}% | "
        f"SSL: {report['thresholds']['ssl_days']} days"
    )
    lines.append("=" * 70)

    for client in report["clients"]:
        lines.append(f"\n  [{client['name']}] (ID: {client['id']})")
        if client["host"]:
            lines.append(f"    Host: {client['host']} | IP: {client['ip']}")
        if client["tags"]:
            lines.append(f"    Tags: {client['tags']}")

        for check_name, check_data in client["checks"].items():
            if check_data.get("code", -1) == -1:
                status_str = "SKIPPED"
            else:
                status_str = check_data.get("worst_exit_code", check_data.get("status", "?"))
                status_map = {0: "✅ OK", 1: "⚠️ WARN", 2: "🔴 CRIT", 3: "❓ UNKNOWN"}
                status_str = status_map.get(status_str, str(status_str))

            if check_name == "disk":
                lines.append(f"    Disk:   {status_str}")
                if "results" in check_data and check_data["results"]:
                    for r in check_data["results"]:
                        if r.get("error"):
                            lines.append(f"      {r['path']}: ERROR — {r['error']}")
                        else:
                            lines.append(
                                f"      {r['path']}: {r['use_pct']}% used — {r['status']}"
                            )

            elif check_name == "ssl":
                lines.append(f"    SSL:    {status_str}")
                if "results" in check_data and check_data["results"]:
                    for r in check_data["results"]:
                        if r.get("error"):
                            lines.append(f"      {r['host']}:{r['port']}: ERROR — {r['error']}")
                        else:
                            lines.append(
                                f"      {r['host']}:{r['port']}: {r['status']} "
                                f"(expires {r.get('not_after','?')}, {r.get('days_remaining','?')}d)"
                            )

            elif check_name == "uptime":
                lines.append(f"    Uptime: {status_str}")
                if "results" in check_data and check_data["results"]:
                    for r in check_data["results"]:
                        ping = r.get("ping")
                        if ping:
                            lines.append(
                                f"      {r['host']} ping: {ping.get('status','?')}"
                            )

    lines.append("\n" + "=" * 70)
    overall_map = {0: "ALL SYSTEMS GO", 1: "DEGRADED — ACTION RECOMMENDED", 2: "CRITICAL — IMMEDIATE ACTION REQUIRED", 3: "UNKNOWN — INVESTIGATE"}
    lines.append(f"  Overall Status: {overall_map.get(report['overall_worst_exit_code'], '?')}")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    args = parse_args()
    logger = setup_logging()
    logger.info("Starting client health report generation")

    report = generate_report(args, logger)

    if args.format == "json":
        output = json.dumps(report, indent=2)
    else:
        output = format_text_report(report)

    print(output)

    if args.output:
        args.output.write_text(output)
        logger.info(f"Report written to {args.output}")

    # Exit with worst code from any client
    sys.exit(report["overall_worst_exit_code"])


if __name__ == "__main__":
    main()
