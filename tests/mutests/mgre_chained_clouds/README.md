# Chained-clouds mutest: all five GRE/mGRE modes end to end

One network, five segments in series, each using a different
GRE/mGRE peer-resolution mode, connected only by nrlsmf relaying
(never by IP routing):

```

    ha   ha2         hb2        hc2                        hf
    |     |          |          |                          |
    |     A2         B2         C2                   E2    |
    |     |          |          |                    |     |
    A -- u0 -- B -- u1 -- C -- u2 -- D -- u3 -- E -- u4 -- F

```

| Segment | Underlay | Mode | Peers |
|---------|----------|------|-------|
| cloud0 | u0 | Static NBMA mGRE | A, A2, B |
| cloud1 | u1 | NHRP-resolved mGRE | B, B2 (hub/NHS), C |
| cloud2 | u2 | Multicast-underlay mGRE | C, C2, D |
| cloud3 | u3 | Point-to-point GRE | D, E |
| cloud4 | u4 | External (metadata) GRE | E, E2, F |

Application multicast is sourced on `ha` (off `A`) and received on
`ha2` (cloud0), `hb2` (cloud1), `hc2` (cloud2), and `hf` (cloud4).
Each SMF router with a host CFs its host LAN plus its GRE iface so
nrlsmf is first hop onto the overlay and last hop off it. `B`, `C`,
`D`, and `E` CF both overlay ifaces. Overlay GRE ifaces are `layered`
except cloud1's NHRP hub `B2`: nhrpd only programs spoke→hub and
hub→spokes, so `B2` must replicate overlay multicast onto `gre_c1`.
`u2` runs `rmerge` only as a stand-in for underlay multicast routing (PIM).

`u0` through `u4` are five separate, disconnected underlay routers —
there is no IP route anywhere in this topology from one segment's
addressing to the next. A multicast packet from `ha` can only reach
`hf` by being relayed hop-by-hop through nrlsmf on `A`, then `B`,
then `C`, then `D`, then `E`.

Overlay-multicast inject dests are mixed: `map …,dynamic` on A
(cloud0) and on B/B2/C (cloud1; B2 is the hub replicator). Explicit
unicast `map`s on A2/B (cloud0) and every cloud4 node. cloud2 uses
`ujoin`; cloud3 (P2P) needs no `map`.

## Why this exists

Every mode in `../mgre_four_peers/` is tested in isolation. This
topology chains all five together the way a real deployment might —
e.g. a MANET island (static NBMA), gatewayed through a DMVPN-style WAN
(NHRP), onward through a satellite hop (multicast-underlay), and into
an SDN-managed segment (external/metadata GRE) — and proves one
multicast flow can cross all of them.

## Run

From `tests/mutests` (requires root, FRR with `nhrpd` enabled, `nrlsmf`
on PATH):

```bash
sudo mutest mgre_chained_clouds
```

Skipped on Linux < 5.0 (collect-md last segment).
