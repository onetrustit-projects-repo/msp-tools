#!/usr/bin/env python3
"""
check-disk.py — Monitor disk usage on local or remote hosts via SSH.

Exit codes:
    0 = OK (all paths within thresholds)
    1 = WARN (one or more paths exceed warning threshold)
    2 = CRIT (one or more paths exceed critical threshold)
    3 = UNKNOWN (error — host unreachable, path not found, etc.)
"""

import argparse
import json
import os
import sys
import datetime
import logging
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.stderr.write("Error: paramiko not installed. Run: pip install paramiko\n")
    sys.exit(3)

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging():
    log_file = LOG_DIR / f"check-disk-{datetime.date.today().isoformat()}.log"
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
        description="Check disk usage on a remote host via SSH.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --host server1.example.com --user admin --path /
  %(prog)s --host server1.example.com --path / --path /home --warn 80 --crit 90
  %(prog)s --host 192.168.1.10 --port 2222 --path /data --crit 95
  %(prog)s --host server1.example.com --json
        """,
    )
    parser.add_argument("--host", required=True, help="Remote hostname or IP")
    parser.add_argument("--user", default="root", help="SSH username (default: root)")
    parser.add_argument(
        "--port", type=int, default=22, help="SSH port (default: 22)"
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        dest="paths",
        help="Mount point path(s) to check (default: /)",
    )
    parser.add_argument(
        "--warn",
        type=int,
        default=80,
        help="Warning threshold %% used (default: 80)",
    )
    parser.add_argument(
        "--crit",
        type=int,
        default=90,
        help="Critical threshold %% used (default: 90)",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=Path(os.path.expanduser("~/.ssh/id_rsa")),
        help="SSH private key path",
    )
    parser.add_argument(
        "--timeout", type=int, default=10, help="SSH connection timeout (default: 10s)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    return parser.parse_args()


def ssh_connect(host, user, port, key_path, timeout):
    """Establish SSH connection and return client."""
    logger = logging.getLogger(__name__)
    try:
        key = paramiko.RSAKey.from_private_key_file(str(key_path))
    except FileNotFoundError:
        logger.error(f"SSH key not found: {key_path}")
        raise RuntimeError(f"SSH key not found: {key_path}")
    except paramiko.PasswordRequiredException:
        raise RuntimeError("SSH key is encrypted — provide an unencrypted key")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        pkey=key,
        timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def get_disk_usage(ssh_client, path):
    """Run `df -h` on remote host and parse output for the given path."""
    logger = logging.getLogger(__name__)
    # Use awk to get the entry for the given mount point (handles spaces in mount points)
    cmd = f"df -h '{path}' | tail -1"
    stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=30)
    output = stdout.read().decode().strip()
    err = stderr.read().decode().strip()

    if err:
        logger.warning(f"df stderr for {path}: {err}")

    if not output:
        raise RuntimeError(f"No output from df for path: {path}")

    parts = output.split()
    if len(parts) < 6:
        raise RuntimeError(f"Unexpected df output format for {path}: {output}")

    # df output: Filesystem  Size  Used  Avail  Use%  Mounted on
    filesystem = parts[0]
    total = parts[1]
    used = parts[2]
    avail = parts[3]
    use_pct_str = parts[4]
    mounted_on = parts[5]

    use_pct = int(use_pct_str.rstrip("%"))
    return {
        "path": path,
        "filesystem": filesystem,
        "total": total,
        "used": used,
        "avail": avail,
        "use_pct": use_pct,
        "mounted_on": mounted_on,
    }


def check_disk_remote(host, user, port, key_path, paths, warn, crit, timeout):
    logger = logging.getLogger(__name__)
    results = []
    worst_code = 0  # 0=OK

    try:
        client = ssh_connect(host, user, port, key_path, timeout)
        logger.info(f"Connected to {host}")

        for path in paths:
            try:
                usage = get_disk_usage(client, path)
                logger.info(f"{host}:{path} — {usage['use_pct']}% used")

                if usage["use_pct"] >= crit:
                    status = "CRIT"
                    code = 2
                elif usage["use_pct"] >= warn:
                    status = "WARN"
                    code = 1
                else:
                    status = "OK"
                    code = 0

                usage["status"] = status
                usage["code"] = code
                results.append(usage)

                if code > worst_code:
                    worst_code = code

            except Exception as e:
                logger.error(f"Error checking {path} on {host}: {e}")
                results.append(
                    {
                        "path": path,
                        "status": "UNKNOWN",
                        "code": 3,
                        "error": str(e),
                    }
                )
                worst_code = max(worst_code, 3)

        client.close()

    except Exception as e:
        logger.error(f"Connection error to {host}: {e}")
        for path in paths:
            results.append({"path": path, "status": "UNKNOWN", "code": 3, "error": str(e)})
        worst_code = 3

    return results, worst_code


def format_output(results, worst_code, host, warn, crit, json_output=False):
    if json_output:
        out = {
            "host": host,
            "check": "disk",
            "timestamp": datetime.datetime.now().isoformat(),
            "thresholds": {"warn": warn, "crit": crit},
            "worst_exit_code": worst_code,
            "results": results,
        }
        return json.dumps(out, indent=2)

    lines = [f"Disk check — {host}"]
    lines.append(f"Thresholds: warn={warn}%%, crit={crit}%%")
    lines.append("-" * 60)
    for r in results:
        if "error" in r:
            lines.append(f"  {r['path']}: UNKNOWN — {r['error']}")
        else:
            pct = r["use_pct"]
            lines.append(
                f"  {r['path']} [{r['mounted_on']}]: {pct}% used — {r['status']} "
                f"({r['used']}/{r['total']}, {r['avail']} free)"
            )
    lines.append("-" * 60)
    status_map = {0: "OK", 1: "WARN", 2: "CRIT", 3: "UNKNOWN"}
    lines.append(f"Overall: {status_map[worst_code]}")
    return "\n".join(lines)


def main():
    args = parse_args()
    logger = setup_logging()

    if not args.paths:
        args.paths = ["/"]

    logger.info(
        f"Starting disk check — host={args.host}, paths={args.paths}, "
        f"warn={args.warn}%%, crit={args.crit}%%"
    )

    results, worst_code = check_disk_remote(
        host=args.host,
        user=args.user,
        port=args.port,
        key_path=args.key,
        paths=args.paths,
        warn=args.warn,
        crit=args.crit,
        timeout=args.timeout,
    )

    output = format_output(results, worst_code, args.host, args.warn, args.crit, args.json)
    print(output)

    sys.exit(worst_code)


if __name__ == "__main__":
    main()
