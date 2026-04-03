#!/usr/bin/env python3
"""
check-ssl.py — Check TLS/SSL certificate expiration on remote hosts.

Exit codes:
    0 = OK (certificate valid and not expiring within threshold)
    1 = WARN (certificate expiring within --days threshold)
    2 = CRIT (certificate expired or expiring within 24 hours)
    3 = UNKNOWN (connection error, etc.)
"""

import argparse
import json
import socket
import ssl
import sys
import datetime
import logging
import os
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging():
    log_file = LOG_DIR / f"check-ssl-{datetime.date.today().isoformat()}.log"
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
        description="Check SSL/TLS certificate expiration on one or more hosts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --host example.com
  %(prog)s --host example.com --port 443 --days 30
  %(prog)s --hosts hosts.txt --days 14
  %(prog)s --host mail.example.com --port 993 --days 7
  %(prog)s --host example.com --json
        """,
    )
    parser.add_argument(
        "--host",
        help="Single hostname to check",
    )
    parser.add_argument(
        "--hosts",
        type=Path,
        help="File containing host:port pairs (one per line, # for comments)",
    )
    parser.add_argument(
        "--port", type=int, default=443, help="Port to connect to (default: 443)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Warn if cert expires within N days (default: 30)",
    )
    parser.add_argument(
        "--timeout", type=int, default=10, help="Connection timeout in seconds (default: 10)"
    )
    parser.add_argument(
        "--sni",
        help="SNI hostname (defaults to --host if not specified)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    return parser.parse_args()


def load_hosts_file(path):
    """Parse a hosts file: lines like 'example.com:443' or 'example.com' (uses default port)."""
    hosts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                host, port = line.rsplit(":", 1)
                hosts.append((host.strip(), int(port.strip())))
            else:
                hosts.append((line.strip(), 443))
    return hosts


def check_cert(host, port, timeout, sni=None):
    """
    Retrieve the certificate from host:port and return expiry info.
    Returns a dict with: host, port, subject, issuer, not_before, not_after,
    days_remaining, serial_number, signature_algorithm, code, status
    """
    logger = logging.getLogger(__name__)
    target = sni if sni else host

    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    try:
        # Wrap socket
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=target) as ssock:
                cert = ssock.getpeercert(binary_form=True)
                cert_dict = ssock.getpeercert()

        # Parse certificate
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        cert_obj = x509.load_der_x509_certificate(cert, default_backend())

        not_before = cert_obj.not_valid_before_utc
        not_after = cert_obj.not_valid_after_utc
        now = datetime.datetime.now(datetime.timezone.utc)
        days_remaining = (not_after - now).days

        # Subject and issuer
        subject_parts = []
        for attr in cert_obj.subject:
            subject_parts.append(f"{attr.oid._name}={attr.value}")
        subject = ", ".join(subject_parts) if subject_parts else "Unknown"

        issuer_parts = []
        for attr in cert_obj.issuer:
            issuer_parts.append(f"{attr.oid._name}={attr.value}")
        issuer = ", ".join(issuer_parts) if issuer_parts else "Unknown"

        # Serial
        serial = hex(cert_obj.serial_number)

        # Signature algo
        sig_algo = cert_obj.signature_algorithm_oid._name

        # Determine status and code
        if days_remaining < 0:
            status = "EXPIRED"
            code = 2
        elif days_remaining <= 1:
            status = "CRIT"
            code = 2
        elif days_remaining <= args.days:
            status = "WARN"
            code = 1
        else:
            status = "OK"
            code = 0

        logger.info(f"{host}:{port} — expires {not_after.date()} ({days_remaining}d) — {status}")

        return {
            "host": host,
            "port": port,
            "sni": sni or host,
            "subject": subject,
            "issuer": issuer,
            "not_before": not_before.isoformat(),
            "not_after": not_after.isoformat(),
            "days_remaining": days_remaining,
            "serial": serial,
            "signature_algorithm": sig_algo,
            "status": status,
            "code": code,
        }

    except ssl.SSLCertVerificationError as e:
        logger.warning(f"{host}:{port} — certificate verification error: {e}")
        return {
            "host": host,
            "port": port,
            "status": "UNKNOWN",
            "code": 3,
            "error": f"Certificate verification failed: {e}",
        }
    except socket.timeout:
        logger.error(f"{host}:{port} — connection timeout")
        return {
            "host": host,
            "port": port,
            "status": "UNKNOWN",
            "code": 3,
            "error": "Connection timeout",
        }
    except ConnectionRefusedError:
        logger.error(f"{host}:{port} — connection refused")
        return {
            "host": host,
            "port": port,
            "status": "UNKNOWN",
            "code": 3,
            "error": "Connection refused",
        }
    except Exception as e:
        logger.error(f"{host}:{port} — unexpected error: {e}")
        return {
            "host": host,
            "port": port,
            "status": "UNKNOWN",
            "code": 3,
            "error": str(e),
        }


# Store args globally for use in check_cert
args = None


def main():
    global args
    args = parse_args()
    logger = setup_logging()

    # Build host list
    hosts = []
    if args.hosts:
        hosts = load_hosts_file(args.hosts)
        logger.info(f"Loaded {len(hosts)} hosts from {args.hosts}")
    elif args.host:
        hosts = [(args.host, args.port)]
    else:
        sys.stderr.write("Error: specify --host or --hosts\n")
        sys.exit(3)

    results = []
    worst_code = 0

    for host, port in hosts:
        logger.info(f"Checking {host}:{port}")
        result = check_cert(host, port, args.timeout, args.sni)
        results.append(result)
        if result["code"] > worst_code:
            worst_code = result["code"]

    if args.json:
        out = {
            "check": "ssl",
            "timestamp": datetime.datetime.now().isoformat(),
            "warn_days": args.days,
            "worst_exit_code": worst_code,
            "results": results,
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"SSL Certificate Check — {datetime.datetime.now().isoformat()}")
        print(f"Warning threshold: {args.days} days")
        print("-" * 70)
        for r in results:
            if "error" in r:
                print(f"  {r['host']}:{r['port']}: UNKNOWN — {r['error']}")
            else:
                expiry_str = r["not_after"].split("T")[0]
                print(
                    f"  {r['host']}:{r['port']} [{r['sni']}]: "
                    f"{r['status']} — expires {expiry_str} "
                    f"({r['days_remaining']}d remaining)"
                )
                if r["status"] != "OK":
                    print(f"    Subject: {r['subject']}")
                    print(f"    Issuer: {r['issuer']}")
        print("-" * 70)
        status_map = {0: "OK", 1: "WARN", 2: "CRIT", 3: "UNKNOWN"}
        print(f"Overall: {status_map[worst_code]}")

    sys.exit(worst_code)


if __name__ == "__main__":
    main()
