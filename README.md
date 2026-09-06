# a-lab

A self-contained lab for standing up [Akvorado](https://github.com/akvorado/akvorado)
- an open-source NetFlow/sFlow/IPFIX collector - on a single VM with
Docker, plus a set of scripts for enriching Akvorado's peer-traffic
visibility using IRR data.

Originally built as conference lab material (see the slide deck),
this repo also works as a standalone step-by-step guide if you're
setting up Akvorado for the first time.

## Where to start

| If you want... | Go to |
|---|---|
| A full, explained, step-by-step walkthrough | [`akvorado-lab-guide.md`](akvorado-lab-guide.md) |
| Just the commands, no explanation (you've done this before) | [`lazy-guide.md`](lazy-guide.md) |
| HTTPS + basic auth in front of the console | `akvorado-lab-guide.md`, Appendix B |
| To remove everything cleanly | `akvorado-lab-guide.md`, Appendix A |
| Conference/presentation slides covering this material | [`Akvorado-lab-slides.pptx`](Akvorado-lab-slides.pptx) |
| Peer-cone enrichment tooling (see below) | [`scripts/`](scripts/) |

## What's in this repo

```
.
├── akvorado-lab-guide.md      Full guide: deploy Akvorado on a VM with Docker,
│                              configure flow listeners, point network devices
│                              (MikroTik/Arista/Juniper/Cisco) at the collector,
│                              troubleshoot, and optionally secure with HTTPS.
├── lazy-guide.md               Same deployment, commands only, no explanation.
├── Akvorado-lab-slides.pptx    Slide deck version of the material.
├── docker/
│   └── docker-compose-https.yaml   Reference compose file with Traefik +
│                                    Let's Encrypt + basic auth wired in
│                                    (see Appendix B in the main guide).
└── scripts/
    ├── cone_query.py            Expand an IRR as-set into a flat ASN list
    │                            and print a ready-to-paste Akvorado filter.
    ├── gen_peer_cone_filters.sh Batch wrapper: run cone_query.py across a
    │                            list of peers and emit a saved-filters
    │                            block for Akvorado's console.yaml.
    ├── peers.example.conf       Template peer list for the wrapper above.
    └── README.md                Full docs for the scripts above.
```

## Prerequisites

- A Linux VM (Ubuntu assumed throughout) with root/sudo access
- Docker + Docker Compose (the guide walks through installing these)
- Network devices capable of exporting NetFlow, sFlow, or IPFIX
  (MikroTik, Arista, Juniper, and Cisco are covered explicitly, but
  anything that speaks one of those protocols will work)

See `akvorado-lab-guide.md` section 3 for exact VM specs and firewall
port requirements before you start.

## The scripts folder, briefly

Akvorado only knows a flow's immediate Src/DstAS - it has no concept
of "this peer plus everyone downstream of them." If you peer with a
transit provider or an ISP, filtering on their bare ASN alone
undercounts your actual traffic exchange with them.

`scripts/cone_query.py` expands a peer's IRR as-set into their full
customer cone (recursively, across multiple registries, with
retry/failover if a registry resets the connection under load), and
`gen_peer_cone_filters.sh` runs it across your whole peer list in one
go, producing a block you paste into `console.yaml`. See
[`scripts/README.md`](scripts/README.md) for full usage, options, and
troubleshooting.

## Contributing

No formal process here - fork, branch, and open a PR. If you're
contributing a peer-list-shaped file (like a filled-in `peers.conf`),
double check you're not accidentally committing your own network's
real peer relationships; `peers.example.conf` and `.gitignore` exist
so real peer lists stay local.
