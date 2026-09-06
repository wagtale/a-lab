#!/usr/bin/env python3
"""
cone_query.py - Expand an IRR as-set into a flat ASN list and print
a ready-to-paste Akvorado filter clause.

Uses the raw IRRd whois protocol directly (no bgpq4 dependency).
Resolution is done client-side, recursively: for each object we ask
"!i<object>" (non-recursive members) at each registry in --hosts, in
order, and recurse into any member that isn't a plain ASN.

This matters because a single IRRd host's own "!i<object>,1" recursion
only follows objects it can find in its own (mirrored) dataset. If your
as-set's tree crosses registries -- e.g. a RADB-hosted object whose
member is an AFRINIC-only nested set -- single-host recursion silently
stops there. Querying multiple registries per unresolved object (the
same problem bgpq4's -S flag solves) avoids that.

Concurrency: independent branches of the as-set tree are resolved in
separate threads. One thread is spawned per nested object rather than
drawing from a fixed-size pool -- with a fixed pool, a parent task can
occupy a worker slot while blocked waiting on a child task that itself
needs a free worker slot, and a deep/bushy tree can deadlock the pool
outright. Native threads don't have that problem: every node always
gets a thread to run in. What IS bounded is *concurrent network I/O*,
via a semaphore (--workers), so a big tree doesn't open hundreds of
simultaneous sockets against someone else's whois server at once.

Reliability: public whois servers -- RADB in particular -- will reset
connections under load rather than queue them. A reset on one host
fails over immediately to the next host in --hosts for that object; if
every host errors in one pass, the whole host list is retried with
exponential backoff + jitter (up to 3 attempts) before giving up on
that branch. This matters because an earlier version of this script
let an unhandled ConnectionResetError kill the worker thread outright,
which silently dropped that branch's ASNs from the final result with
no warning at all -- undercounting large trees with no indication
anything had gone wrong.

Examples:
    # Wolcomm's own maintained peer object (RADB), nested set lives in AFRINIC
    ./cone_query.py AS37271:AS-PEERS:AS37100

    # Filter on SrcAS instead of DstAS
    ./cone_query.py --field SrcAS AS37271:AS-PEERS:AS37100

    # Emit both directions combined
    ./cone_query.py --both AS37271:AS-PEERS:AS37100

    # Add more registries to try, e.g. if a branch lives in RIPE/APNIC/ARIN
    ./cone_query.py --hosts whois.radb.net,whois.afrinic.net,whois.ripe.net,rr.ntt.net AS37271:AS-PEERS:AS37100

    # Watch it work, with up to 16 concurrent whois connections
    ./cone_query.py --verbose --workers 16 AS-SET-SEACOM
"""

import argparse
import random
import re
import socket
import sys
import threading
import time

DEFAULT_HOSTS = ["whois.radb.net", "whois.afrinic.net", "rr.ntt.net"]

# IRRd returns AS numbers in RPSL "ASnnnn" form, not bare digits -- match both
# just in case a given server ever returns the bare form.
ASN_RE = re.compile(r"^AS(\d+)$", re.IGNORECASE)


def irrd_query(host: str, port: int, command: str, timeout: float = 10.0) -> str:
    """Send a raw IRRd query and return the decoded response.

    Raises ConnectionError/OSError (e.g. ConnectionResetError) on
    network failure -- callers are responsible for catching these and
    failing over to another host, since a single flaky registry
    shouldn't take down resolution of the whole tree.
    """
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


class ConeResolver:
    """Holds the shared state for one resolve() run: the visited set
    (dedup + cycle protection), a query counter for verbose progress,
    and a semaphore capping concurrent whois connections."""

    def __init__(self, hosts, port, max_depth, max_workers, verbose):
        self.hosts = hosts
        self.port = port
        self.max_depth = max_depth
        self.verbose = verbose
        self.visited: set[str] = set()
        self.visited_lock = threading.Lock()
        self.sem = threading.Semaphore(max_workers)
        self.query_count = 0
        self.count_lock = threading.Lock()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    def resolve(self, object_name: str, depth: int = 0) -> set[int]:
        key = object_name.upper()
        with self.visited_lock:
            if key in self.visited:
                return set()
            self.visited.add(key)

        if depth > self.max_depth:
            print(f"warning: max recursion depth hit at {object_name}", file=sys.stderr)
            return set()

        indent = "  " * depth
        tokens: list[str] = []
        used_host = None
        had_network_errors = False

        # Try the whole pool of servers up to 3 times. A single pass tries
        # each host in order for this object; if every host in a pass
        # errors out (rather than cleanly answering "not found"), the
        # whole pool is retried with backoff -- this recovers from a
        # transient blip (e.g. rate limiting) hitting all registries at
        # once, which a single-host-then-give-up approach wouldn't.
        for attempt in range(3):
            network_errors = 0

            for host in self.hosts:
                with self.sem:
                    with self.count_lock:
                        self.query_count += 1
                        n = self.query_count

                    t0 = time.time()
                    try:
                        raw = irrd_query(host, self.port, f"!i{object_name}")
                        elapsed = time.time() - t0

                        if raw.startswith("A"):
                            tokens = parse_member_tokens(raw)
                            self._log(f"{indent}[#{n:04d} d{depth}] {object_name} @ {host}: {len(tokens)} members ({elapsed:.2f}s)")
                            used_host = host
                            break  # found it, stop trying other hosts
                        elif raw.startswith("D"):
                            self._log(f"{indent}[#{n:04d} d{depth}] {object_name} @ {host}: not found ({elapsed:.2f}s)")
                            continue  # clean "not found" -- try next host
                        else:
                            continue  # unexpected response -- try next host

                    except (ConnectionError, OSError):
                        elapsed = time.time() - t0
                        network_errors += 1
                        had_network_errors = True
                        self._log(f"{indent}[#{n:04d} d{depth}] {object_name} @ {host}: connection error ({elapsed:.2f}s) -> failing over to next host")
                        continue

            if tokens:
                break  # resolved -- done, no need to retry the pool

            if network_errors == 0:
                # every host answered cleanly and none had it -- it
                # genuinely doesn't exist under this name, no point retrying
                break

            # every host in this pass errored (or a mix of errors and
            # clean misses with zero successes) -- give the registries a
            # moment and retry the whole pool, unless we're out of attempts
            if attempt < 2:
                sleep_time = (2 ** attempt) + random.uniform(0.1, 1.0)
                self._log(f"{indent}  [!] network errors resolving {object_name}, retrying pool in {sleep_time:.1f}s...")
                time.sleep(sleep_time)

        if not tokens:
            if had_network_errors:
                print(
                    f"warning: gave up on {object_name} after network errors across "
                    f"{', '.join(self.hosts)} (3 attempts)",
                    file=sys.stderr,
                )
            else:
                print(f"warning: could not resolve {object_name} against {', '.join(self.hosts)}", file=sys.stderr)
            return set()

        asns: set[int] = set()
        sub_objects: list[str] = []
        for tok in tokens:
            m = ASN_RE.match(tok)
            if m:
                asns.add(int(m.group(1)))
            else:
                sub_objects.append(tok)

        if sub_objects:
            self._log(f"{indent}  -> {object_name} ({used_host}) has {len(sub_objects)} nested object(s), spawning threads")
            results: dict[str, set[int]] = {}
            results_lock = threading.Lock()

            def worker(obj: str) -> None:
                r = self.resolve(obj, depth + 1)
                with results_lock:
                    results[obj] = r

            threads = [threading.Thread(target=worker, args=(obj,)) for obj in sub_objects]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for r in results.values():
                asns |= r

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
    ap.add_argument("--max-depth", type=int, default=12, help="max recursion depth (default: 12)")
    ap.add_argument(
        "-w", "--workers", type=int, default=8,
        help="max concurrent whois connections (default: 8). Higher is faster on a "
             "large/bushy tree but more load on the remote registries -- keep it modest.",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true",
        help="print every query as it happens (depth, object, host, hit/miss, timing) so "
             "you can see it's actually working on a large tree, instead of just waiting.",
    )
    args = ap.parse_args()

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]

    resolver = ConeResolver(
        hosts=hosts,
        port=args.port,
        max_depth=args.max_depth,
        max_workers=args.workers,
        verbose=args.verbose,
    )

    start = time.time()
    asns = sorted(resolver.resolve(args.object))
    elapsed = time.time() - start

    if not asns:
        print(f"\nNo ASNs resolved for {args.object} across {', '.join(hosts)}.", file=sys.stderr)
        sys.exit(1)

    print(
        f"# {len(asns)} ASNs in {args.object} (across {', '.join(hosts)}, "
        f"{resolver.query_count} queries, {elapsed:.1f}s, workers={args.workers})",
        file=sys.stderr,
    )

    asn_list = ", ".join(str(a) for a in asns)
    if args.both:
        print(f"(SrcAS IN ({asn_list}) OR DstAS IN ({asn_list}))")
    else:
        print(f"{args.field} IN ({asn_list})")


if __name__ == "__main__":
    main()
