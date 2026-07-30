#!/usr/bin/env python3
"""
cone_query.py - Expand an IRR as-set into a flat ASN list and print
a ready-to-paste Akvorado filter clause.

Uses the raw IRRd whois protocol directly (no bgpq4 dependency).
Resolution is done client-side, recursively: for each object we ask
"!i<object>" (non-recursive members) at each registry in --hosts,
in order, and recurse into any member that isn't a plain ASN.

This matters because a single IRRd host's own "!i<object>,1" recursion
only follows objects it can find in its own (mirrored) dataset. If your
as-set's tree crosses registries -- e.g. a RADB-hosted object whose
member is an AFRINIC-only nested set -- single-host recursion silently
stops there. Querying multiple registries per unresolved object (the
same problem bgpq4's -S flag solves) avoids that.

Examples:
    # Wolcomm's own maintained peer object (RADB), nested set lives in AFRINIC
    ./cone_query.py AS37271:AS-PEERS:AS37100

    # Filter on SrcAS instead of DstAS
    ./cone_query.py --field SrcAS AS37271:AS-PEERS:AS37100

    # Emit both directions combined
    ./cone_query.py --both AS37271:AS-PEERS:AS37100

    # Add more registries to try, e.g. if a branch lives in RIPE/APNIC/ARIN
    ./cone_query.py --hosts whois.radb.net,whois.afrinic.net,whois.ripe.net,rr.ntt.net AS37271:AS-PEERS:AS37100
"""

import argparse
import re
import socket
import sys

DEFAULT_HOSTS = ["whois.radb.net", "whois.afrinic.net", "rr.ntt.net"]

# IRRd returns AS numbers in RPSL "ASnnnn" form, not bare digits -- match both
# just in case a given server ever returns the bare form.
ASN_RE = re.compile(r"^AS(\d+)$", re.IGNORECASE)


def irrd_query(host: str, port: int, command: str, timeout: float = 10.0) -> str:
    """Send a raw IRRd query and return the decoded response."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall((command + "\r\n").encode())
        buf = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # IRRd terminates a response with a line containing just "C"
                if buf.rstrip().endswith(b"C"):
                    break
        except socket.timeout:
            pass
    return buf.decode(errors="replace")


def parse_member_tokens(raw: str) -> list[str]:
    """Parse an IRRd 'A<len>\\n<data>\\nC' response into raw member tokens
    (these can be ASNs or nested as-set/route-set names)."""
    if not raw.startswith("A"):
        return []  # "D" (not found), "F" (error), or garbage -- treat as unresolved
    header, _, rest = raw.partition("\n")
    try:
        length = int(header[1:].strip())
    except ValueError:
        return []
    return rest[:length].split()


def resolve_cone(
    object_name: str,
    hosts: list[str],
    port: int,
    visited: set | None = None,
    depth: int = 0,
    max_depth: int = 12,
) -> set[int]:
    """Recursively resolve an as-set into a flat set of origin ASNs,
    trying each host in order for every unresolved object."""
    if visited is None:
        visited = set()
    key = object_name.upper()
    if key in visited:
        return set()
    visited.add(key)
    if depth > max_depth:
        print(f"warning: max recursion depth hit at {object_name}", file=sys.stderr)
        return set()

    tokens: list[str] = []
    for host in hosts:
        tokens = parse_member_tokens(irrd_query(host, port, f"!i{object_name}"))
        if tokens:
            break

    if not tokens:
        print(f"warning: could not resolve {object_name} against {', '.join(hosts)}", file=sys.stderr)
        return set()

    asns: set[int] = set()
    for tok in tokens:
        m = ASN_RE.match(tok)
        if m:
            asns.add(int(m.group(1)))
        else:
            asns |= resolve_cone(tok, hosts, port, visited, depth + 1, max_depth)
    return asns


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("object", help="AS-SET (or ASN) to expand, e.g. AS37271:AS-PEERS:AS37100")
    ap.add_argument(
        "--hosts",
        default=",".join(DEFAULT_HOSTS),
        help=f"comma-separated IRRd hosts to try per unresolved object (default: {','.join(DEFAULT_HOSTS)})",
    )
    ap.add_argument("--port", type=int, default=43)
    ap.add_argument("--field", default="DstAS", help="Akvorado field to filter on (default: DstAS)")
    ap.add_argument("--both", action="store_true", help="Emit SrcAS OR DstAS instead of a single field")
    args = ap.parse_args()

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    asns = sorted(resolve_cone(args.object, hosts, args.port))

    if not asns:
        print(f"\nNo ASNs resolved for {args.object} across {', '.join(hosts)}.", file=sys.stderr)
        sys.exit(1)

    print(f"# {len(asns)} ASNs in {args.object} (across {', '.join(hosts)})", file=sys.stderr)

    asn_list = ", ".join(str(a) for a in asns)
    if args.both:
        print(f"(SrcAS IN ({asn_list}) OR DstAS IN ({asn_list}))")
    else:
        print(f"{args.field} IN ({asn_list})")


if __name__ == "__main__":
    main()
