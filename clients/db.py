#!/usr/bin/env python3
"""
clients/db.py — MSP Client Registry (SQLite).

Manages client records: name, contact email, hostname, IP, SSH port, notes, tags.
All data stored in ~/msp-tools/data/clients.db

Commands:
    add     Add a new client
    list    List clients (optionally filtered)
    show    Show full details of a client
    update  Update a client
    delete  Delete a client
    export  Export to CSV or JSON
"""

import argparse
import csv
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "clients.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    contact    TEXT,
    host       TEXT,
    ip         TEXT,
    port       INTEGER DEFAULT 22,
    notes      TEXT,
    tags       TEXT,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name);
CREATE INDEX IF NOT EXISTS idx_clients_tags ON clients(tags);
"""


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now_iso():
    return datetime.datetime.now().isoformat()


# ─── Commands ────────────────────────────────────────────────────────────────


def cmd_add(args):
    """Add a new client."""
    conn = get_db()
    tags = ",".join(args.tag) if args.tag else ""
    cur = conn.execute(
        """INSERT INTO clients (name, contact, host, ip, port, notes, tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (args.name, args.contact, args.host, args.ip, args.port, args.notes, tags, now_iso(), now_iso()),
    )
    conn.commit()
    client_id = cur.lastrowid
    print(f"Added client #{client_id}: {args.name}")
    conn.close()


def cmd_list(args):
    """List clients, optionally filtered by tag or search term."""
    conn = get_db()

    query = "SELECT id, name, contact, host, ip, port, tags FROM clients WHERE 1=1"
    params = []

    if args.tag:
        # Tag filter: match any of the provided tags
        tag_conditions = " OR ".join(["tags LIKE ?" for _ in args.tag])
        query += f" AND ({tag_conditions})"
        params.extend([f"%{t}%" for t in args.tag])

    if args.search:
        query += " AND (name LIKE ? OR contact LIKE ? OR host LIKE ?)"
        params.extend([f"%{args.search}%"] * 3)

    query += " ORDER BY name"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("No clients found.")
        return

    print(f"{'ID':<4} {'Name':<30} {'Contact':<30} {'Host':<25} {'IP':<16} {'Tags'}")
    print("-" * 110)
    for r in rows:
        tags_str = r["tags"] or ""
        print(
            f"{r['id']:<4} {r['name']:<30} {(r['contact'] or ''):<30} "
            f"{(r['host'] or ''):<25} {(r['ip'] or ''):<16} {tags_str}"
        )
    print(f"\nTotal: {len(rows)} client(s)")


def cmd_show(args):
    """Show full details of a client by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (args.id,)).fetchone()
    conn.close()

    if not row:
        print(f"Client #{args.id} not found.")
        sys.exit(1)

    print(f"Client #{row['id']}")
    print("=" * 60)
    print(f"  Name:     {row['name']}")
    print(f"  Contact:  {row['contact'] or '(none)'}")
    print(f"  Host:     {row['host'] or '(none)'}")
    print(f"  IP:       {row['ip'] or '(none)'}")
    print(f"  SSH Port: {row['port']}")
    print(f"  Tags:     {row['tags'] or '(none)'}")
    print(f"  Notes:    {row['notes'] or '(none)'}")
    print(f"  Created:  {row['created_at']}")
    print(f"  Updated: {row['updated_at']}")


def cmd_update(args):
    """Update a client record."""
    conn = get_db()

    # Build dynamic UPDATE based on provided args
    fields = []
    params = []

    if args.name is not None:
        fields.append("name = ?")
        params.append(args.name)
    if args.contact is not None:
        fields.append("contact = ?")
        params.append(args.contact)
    if args.host is not None:
        fields.append("host = ?")
        params.append(args.host)
    if args.ip is not None:
        fields.append("ip = ?")
        params.append(args.ip)
    if args.port is not None:
        fields.append("port = ?")
        params.append(args.port)
    if args.notes is not None:
        fields.append("notes = ?")
        params.append(args.notes)
    if args.tag is not None:
        fields.append("tags = ?")
        params.append(",".join(args.tag))

    if not fields:
        print("No fields to update. Provide at least one field to change.")
        sys.exit(1)

    fields.append("updated_at = ?")
    params.append(now_iso())
    params.append(args.id)

    cur = conn.execute(
        f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", params
    )
    conn.commit()
    if cur.rowcount == 0:
        print(f"Client #{args.id} not found.")
        sys.exit(1)
    else:
        print(f"Updated client #{args.id}")
    conn.close()


def cmd_delete(args):
    """Delete a client by ID."""
    conn = get_db()
    cur = conn.execute("DELETE FROM clients WHERE id = ?", (args.id,))
    conn.commit()
    if cur.rowcount == 0:
        print(f"Client #{args.id} not found.")
        sys.exit(1)
    else:
        print(f"Deleted client #{args.id}")
    conn.close()


def cmd_export(args):
    """Export clients to CSV or JSON."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM clients ORDER BY name").fetchall()
    conn.close()

    if not rows:
        print("No clients to export.")
        return

    records = [dict(r) for r in rows]

    if args.format == "json":
        output = json.dumps(records, indent=2)
        if args.output:
            Path(args.output).write_text(output)
            print(f"Exported {len(records)} clients to {args.output}")
        else:
            print(output)
    else:
        if args.output:
            with open(args.output, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
            print(f"Exported {len(records)} clients to {args.output}")
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)


# ─── CLI Parser ──────────────────────────────────────────────────────────────

def make_parser():
    parser = argparse.ArgumentParser(
        description="MSP Client Registry — manage client records in SQLite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p = sub.add_parser("add", help="Add a new client")
    p.add_argument("--name", required=True, help="Client/company name")
    p.add_argument("--contact", help="Contact email")
    p.add_argument("--host", help="Primary hostname")
    p.add_argument("--ip", help="Primary IP address")
    p.add_argument("--port", type=int, default=22, help="SSH port (default: 22)")
    p.add_argument("--notes", help="Notes")
    p.add_argument("--tag", action="append", dest="tag", help="Tag(s) (can repeat)")
    p.set_defaults(func=cmd_add)

    # list
    p = sub.add_parser("list", help="List clients")
    p.add_argument("--tag", action="append", help="Filter by tag(s)")
    p.add_argument("--search", help="Search in name/contact/host")
    p.set_defaults(func=cmd_list)

    # show
    p = sub.add_parser("show", help="Show client details")
    p.add_argument("--id", type=int, required=True, help="Client ID")
    p.set_defaults(func=cmd_show)

    # update
    p = sub.add_parser("update", help="Update a client")
    p.add_argument("--id", type=int, required=True, help="Client ID")
    p.add_argument("--name", help="New name")
    p.add_argument("--contact", help="New contact email")
    p.add_argument("--host", help="New hostname")
    p.add_argument("--ip", help="New IP")
    p.add_argument("--port", type=int, help="New SSH port")
    p.add_argument("--notes", help="New notes")
    p.add_argument("--tag", action="append", help="Replace tags")
    p.set_defaults(func=cmd_update)

    # delete
    p = sub.add_parser("delete", help="Delete a client")
    p.add_argument("--id", type=int, required=True, help="Client ID")
    p.set_defaults(func=cmd_delete)

    # export
    p = sub.add_parser("export", help="Export clients to CSV or JSON")
    p.add_argument("--format", choices=["csv", "json"], default="csv", help="Output format")
    p.add_argument("--output", help="Output file (stdout if not specified)")
    p.set_defaults(func=cmd_export)

    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
