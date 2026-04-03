#!/usr/bin/env python3
"""
check-uptime.py — Ping (ICMP) and/or TCP port availability monitoring.

Exit codes:
    0 = UP (all hosts/ports responding)
    1 = DEGRADED (some hosts/ports degraded — retries used)
    2 = DOWN (host/port unreachable)
    3 = UNKNOWN (error — invalid host, etc.)
"""

import argparse
import json
import socket
import subprocess
import sys
import datetime
import logging
import os
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging():
    log_file = LOG_DIR / f"check-uptime-{datetime.date.today().isoformat()}.log"
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
        description="Check host availability via ICMP ping and/or TCP port check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --host 192.168.1.1 --ping
  %(prog)s --host example.com --port 22 --port 443
  %(prog)s --host example.com --ping --port 22 --port 443
  %(prog)s --hosts hosts.txt --ping
  %(prog)s --host example.com --json
        """,
    )
    parser.add_argument(
        "--host",
        help="Single hostname or IP to check",
    )
    parser.add_argument(
        "--hosts",
        type=Path,
        help="File containing hosts (one per line, # for comments, or host:port format)",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="Perform ICMP ping check",
    )
    parser.add_argument(
        "--port",
        action="append",
        type=int,
        default=[],
        dest="ports",
        help="TCP port(s) to check (can be specified multiple times)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Number of ping retries on failure (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Connection timeout per port in seconds (default: 5)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    return parser.parse_args()


def load_hosts_file(path):
    """Parse a hosts file."""
    hosts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            hosts.append(line.strip())
    return hosts


def ping_host(host, retries, timeout):
    """
    Perform ICMP ping. Returns (success: bool, rtt_ms: float or None, retries_used: int)
    """
    ping_cmd = ["ping", "-c", "1", "-W", str(timeout), host]
    try:
        result = subprocess.run(
            ping_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
        if result.returncode == 0:
            # Try to extract RTT from output
            try:
                out = subprocess.check_output(ping_cmd, stderr=subprocess.DEVNULL, text=True)
                # Example: "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms"
                if "rtt" in out or "round-trip" in out:
                    import re
                    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", out)
                    if m:
                        return True, float(m.group(2)), 0
                # Fallback: assume success if returncode is 0
                return True, None, 0
            except Exception:
                return True, None, 0
        else:
            return False, None, retries
    except subprocess.TimeoutExpired:
        return False, None, retries
    except FileNotFoundError:
        # ping command not found (some systems block ICMP)
        # Try alternative: use python socket connect
        return None, None, 0  # signal that ping is unavailable


def check_port(host, port, timeout):
    """
    Check if a TCP port is open. Returns (open: bool, response_ms: float or None)
    """
    start = datetime.datetime.now()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        elapsed_ms = (datetime.datetime.now() - start).total_seconds() * 1000
        return (result == 0, elapsed_ms)
    except socket.timeout:
        return False, None
    except socket.gaierror:
        return False, None
    except Exception:
        return False, None


def check_host(host, ping_flag, ports, retries, timeout):
    """
    Check a single host: ping + port checks.
    Returns dict with results per check type.
    """
    logger = logging.getLogger(__name__)
    result = {
        "host": host,
        "ping": None,
        "ports": {},
    }
    overall_code = 0
    retries_used = 0

    # Ping check
    if ping_flag:
        ping_ok, rtt, retried = ping_host(host, retries, timeout)
        if ping_ok is None:
            # ping command not available
            result["ping"] = {
                "status": "UNKNOWN",
                "code": 3,
                "error": "ping command not available",
            }
            overall_code = max(overall_code, 3)
        elif ping_ok:
            result["ping"] = {
                "status": "UP",
                "code": 0,
                "rtt_ms": rtt,
            }
        else:
            retries_used = retried
            result["ping"] = {
                "status": "DOWN",
                "code": 2,
                "retries": retries_used,
            }
            overall_code = max(overall_code, 2)

    # Port checks
    for port in ports:
        port_open, resp_ms = check_port(host, port, timeout)
        if port_open:
            result["ports"][port] = {
                "status": "OPEN",
                "code": 0,
                "response_ms": round(resp_ms, 2) if resp_ms else None,
            }
        else:
            result["ports"][port] = {
                "status": "CLOSED",
                "code": 2,
            }
            overall_code = max(overall_code, 2)

    result["code"] = overall_code
    return result


def main():
    args = parse_args()
    logger = setup_logging()

    # Build host list
    hosts = []
    if args.hosts:
        hosts = load_hosts_file(args.hosts)
        logger.info(f"Loaded {len(hosts)} hosts from {args.hosts}")
    elif args.host:
        hosts = [args.host]
    else:
        sys.stderr.write("Error: specify --host or --hosts\n")
        sys.exit(3)

    if not args.ping and not args.ports:
        sys.stderr.write("Error: specify --ping and/or --port\n")
        sys.exit(3)

    all_results = []
    worst_code = 0

    for host in hosts:
        logger.info(f"Checking host: {host}")
        result = check_host(host, args.ping, args.ports, args.retries, args.timeout)
        all_results.append(result)
        if result["code"] > worst_code:
            worst_code = result["code"]

    if args.json:
        out = {
            "check": "uptime",
            "timestamp": datetime.datetime.now().isoformat(),
            "worst_exit_code": worst_code,
            "results": all_results,
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"Uptime Check — {datetime.datetime.now().isoformat()}")
        print("-" * 70)
        for r in all_results:
            if args.ping and r["ping"]:
                ping_r = r["ping"]
                if ping_r["code"] == 3:
                    print(f"  {r['host']} PING: {ping_r['status']} — {ping_r.get('error')}")
                else:
                    rtt_str = f" ({ping_r.get('rtt_ms')}ms)" if ping_r.get('rtt_ms') else ""
                    print(f"  {r['host']} PING: {ping_r['status']}{rtt_str}")

            if args.ports:
                for port, port_r in r["ports"].items():
                    status_str = port_r["status"]
                    if port_r["code"] == 0:
                        resp = f" ({port_r['response_ms']}ms)" if port_r.get('response_ms') else ""
                        print(f"  {r['host']} PORT {port}: {status_str}{resp}")
                    else:
                        print(f"  {r['host']} PORT {port}: {status_str}")

        print("-" * 70)
        status_map = {0: "ALL UP", 1: "DEGRADED", 2: "DOWN", 3: "UNKNOWN"}
        print(f"Overall: {status_map[worst_code]}")

    sys.exit(worst_code)


if __name__ == "__main__":
    main()
