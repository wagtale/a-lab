# cone_query.py

Expand an IRR as-set into a flat list of origin ASNs, and print a
ready-to-paste [Akvorado](https://github.com/akvorado/akvorado) filter
clause for it.

Typical use: you peer with someone (say, AS37100) and want to graph
total traffic to that peer *and all of its customers* - not just the
peer's own ASN. Akvorado only knows a flow's immediate Src/DstAS, so it
has no native concept of a "customer cone." This script builds one from
your IRR data so you can filter/graph on it directly.

## How it works

It speaks the raw IRRd whois protocol (`!i<object>`) directly - no
`bgpq4` dependency. For a given as-set it fetches the direct members,
then recurses into any member that isn't a plain ASN.

Recursion is done **client-side, across multiple registries**, trying
each host in `--hosts` in order for every unresolved object. This
matters because a single IRRd server's own built-in recursion only
follows objects it has mirrored locally - if your as-set's tree crosses
registries (e.g. a RADB-hosted object whose member is an AFRINIC-only
nested set), single-host recursion silently stops there. This is the
same problem `bgpq4 -S` solves for prefix-list generation; this script
does the equivalent for a flat ASN list.

Resolution is **concurrent**: one thread is spawned per nested object
rather than drawn from a fixed-size pool, so a wide/deep tree doesn't
deadlock waiting on itself. Actual network I/O is throttled separately
via `--workers`, so you control load on the remote registries
independently of tree shape. On a large as-set (e.g. a national/pan-
regional transit provider with dozens of downstream customer objects),
this is the difference between single-digit seconds and 20+ minutes.

Cycle protection is included - some IRR trees do reference back up
through themselves.

## Requirements

Python 3.10+, standard library only (`socket`, `argparse`, `re`,
`threading`). No `pip install` needed.

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

# Watch it work on a large tree, with up to 16 concurrent whois connections
./cone_query.py --verbose --workers 16 AS-SET-SEACOM
```

Output:

```
# 3 ASNs in AS37271:AS-PEERS:AS2484 (across whois.radb.net, whois.afrinic.net, rr.ntt.net, 4 queries, 0.9s, workers=8)
DstAS IN (2484, 2485, 2486)
```

The `# ...` line goes to stderr (a summary, safe to ignore/log); the
filter clause on stdout is what you paste into Akvorado.

With `--verbose`, every query is also logged to stderr as it happens -
depth, object name, host, hit/miss, and timing - so you can see
progress on a large tree instead of wondering if it's stuck:

```
[#0001 d0] AS-SET-SEACOM @ whois.radb.net: 497 members (0.58s)
  -> AS-SET-SEACOM (whois.radb.net) has 135 nested object(s), spawning threads
  [#0011 d1] AS-327885 @ whois.radb.net: 5 members (0.57s)
```

## Options

| Flag           | Default                                          | Description                                    |
|----------------|---------------------------------------------------|-------------------------------------------------|
| `--hosts`      | `whois.radb.net,whois.afrinic.net,rr.ntt.net`     | IRRd hosts to try, in order, per unresolved object |
| `--port`       | `43`                                               | IRRd whois port                                |
| `--field`      | `DstAS`                                            | Akvorado field to filter on                    |
| `--both`       | off                                                 | Emit `(SrcAS IN (...) OR DstAS IN (...))` instead of a single field |
| `--max-depth`  | `12`                                                | Max recursion depth before giving up on a branch |
| `-w, --workers`| `8`                                                 | Max concurrent whois connections. Higher is faster on a large/bushy tree but more load on the remote registries - see [Concurrency & reliability](#concurrency--reliability) below before cranking this up |
| `-v, --verbose`| off                                                 | Print every query as it happens (depth, object, host, hit/miss, timing) |

## Concurrency & reliability

Public whois servers - RADB in particular - will reset connections
under load rather than queue them if too many open at once from the
same source. On a large tree with `--workers` set high, this shows up
as `ConnectionResetError` on individual queries. The script handles
this automatically:

1. A reset on one host **fails over immediately** to the next host in
   `--hosts` for that same object - no data is lost as long as at
   least one host in the list can serve it.
2. If **every** host resets/errors for an object in a single pass, the
   whole host list is **retried with exponential backoff + jitter**
   (up to 3 total attempts) before giving up on that branch.

If you see many `connection reset` lines in `--verbose` output, that's
normal under moderate-to-high `--workers` against RADB specifically -
the retry/failover logic is designed for exactly this and shouldn't
cost you any ASNs in the final result. If you want to avoid triggering
it in the first place, lower `-w` (e.g. to 4).

## Using the output in Akvorado
 
Paste the printed clause directly into the filter box on the Visualize
page for one-off use. To make it persist as a one-click option instead
of re-pasting each time, add it under `database.saved-filters` in
**`console.yaml`** - that's the console service's own config file, and
it ships with example entries already in this exact spot (Akvorado's
default config includes sample filters like "From Netflix" / "From
GAFAM" there). Drop the generated entries in alongside (or in place
of) the examples:
 
```yaml
database:
  saved-filters:
    - description: "Peer cone: seacom (AS-SET-SEACOM)"
      content: "(SrcAS IN (...) OR DstAS IN (...))"
```
 
`gen_peer_cone_filters.sh`'s output is already in this exact format -
paste the whole `database:` block it produces straight in, or merge
just the `saved-filters` list if `console.yaml` already has other keys
under `database:`. Restart the console service to pick up the change:
 
```bash
docker compose restart akvorado-console
```


## Limitations

- Reflects what's **registered**, not live BGP reality - a customer
  added to the peer's IRR object yesterday may take time to show up
  here, and stale entries persist until someone cleans them up.
- `--max-depth` (default 12) caps how deep recursion goes; a branch
  that exceeds it prints a warning and stops there rather than
  continuing indefinitely.
- No caching between runs - every invocation re-resolves the whole
  tree from scratch. For a peer whose cone changes slowly, consider
  re-running on a schedule (e.g. weekly cron) and diffing the output
  rather than re-running before every use.
