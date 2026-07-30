# cone_query.py

Expand an IRR as-set into a flat list of origin ASNs, and print a
ready-to-paste [Akvorado](https://github.com/akvorado/akvorado) filter
clause for it.

Typical use: you peer with someone (say, AS37100) and want to graph
total traffic to that peer *and all of its customers* — not just the
peer's own ASN. Akvorado only knows a flow's immediate Src/DstAS, so it
has no native concept of a "customer cone." This script builds one from
your IRR data so you can filter/graph on it directly.

## How it works

It speaks the raw IRRd whois protocol (`!i<object>`) directly — no
`bgpq4` dependency. For a given as-set it fetches the direct members,
then recurses into any member that isn't a plain ASN.

Recursion is done **client-side, across multiple registries**, trying
each host in `--hosts` in order for every unresolved object. This
matters because a single IRRd server's own built-in recursion only
follows objects it has mirrored locally — if your as-set's tree crosses
registries (e.g. a RADB-hosted object whose member is an AFRINIC-only
nested set), single-host recursion silently stops there. This is the
same problem `bgpq4 -S` solves for prefix-list generation; this script
does the equivalent for a flat ASN list.

Cycle protection is included — some IRR trees do reference back up
through themselves.

## Requirements

Python 3.10+, standard library only (`socket`, `argparse`, `re`). No
`pip install` needed.

## Usage

```bash
# Expand an as-set and default to filtering on DstAS
./cone_query.py AS37271:AS-PEERS:AS37100

# Filter on SrcAS instead
./cone_query.py --field SrcAS AS37271:AS-PEERS:AS37100

# Emit both directions combined: (SrcAS IN (...) OR DstAS IN (...))
./cone_query.py --both AS37271:AS-PEERS:AS37100

# Add more registries to the fallback chain, e.g. if a nested set lives in RIPE
./cone_query.py --hosts whois.radb.net,whois.afrinic.net,whois.ripe.net,rr.ntt.net AS37271:AS-PEERS:AS37100
```

Output:

```
# 3 ASNs in AS37271:AS-PEERS:AS2484 (across whois.radb.net, whois.afrinic.net, rr.ntt.net)
DstAS IN (2484, 2485, 2486)
```

The `# ...` line goes to stderr (a summary, safe to ignore/log); the
filter clause on stdout is what you paste into Akvorado.

## Options

| Flag       | Default                                          | Description                                    |
|------------|---------------------------------------------------|-------------------------------------------------|
| `--hosts`  | `whois.radb.net,whois.afrinic.net,rr.ntt.net`     | IRRd hosts to try, in order, per unresolved object |
| `--port`   | `43`                                               | IRRd whois port                                |
| `--field`  | `DstAS`                                            | Akvorado field to filter on                    |
| `--both`   | off                                                 | Emit `(SrcAS IN (...) OR DstAS IN (...))` instead of a single field |

## Using the output in Akvorado

Paste the printed clause directly into the filter box on the Visualize
page. If you want it to persist as a one-click option rather than
re-pasting each time, save it as a named filter from the UI (or add it
under `database.saved-filters` in the Akvorado config).

## Limitations

- Reflects what's **registered**, not live BGP reality — a customer
  added to the peer's IRR object yesterday may take time to show up
  here, and stale entries persist until someone cleans them up.
- Sequential, one TCP connection per object — fine for typical as-set
  sizes, but a very large/deep tree will take noticeably longer since
  connections aren't pipelined or reused.
- Default recursion depth is 12; deeper trees print a warning and stop
  (`--hosts` won't help here — this would need a code change to raise
  `max_depth` in `resolve_cone()`).

