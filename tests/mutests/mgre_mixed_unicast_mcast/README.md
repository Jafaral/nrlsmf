# Mixed underlay unicast/multicast mGRE mutest

Five routers (r0..r4) share one mGRE overlay (`remote 0.0.0.0`,
`172.16.0.0/24`). The underlay is mixed: r0/r1/r2 can take GRE-in-
multicast, r3/r4 cannot.

```
       h0 -- r0 -- lan0 --\     mcast-capable
       h1 -- r1 -- lan1 ---\
       h2 -- r2 -- lan2 ---- u0   rmerge eth0,eth1,eth2 only
       h3 -- r3 -- lan3 ---/     unicast-only
       h4 -- r4 -- lan4 --/
```

`u0` unicasts among all five LANs. Its nrlsmf `rmerge` is only on the
three mcast-capable LANs (PIM stand-in). r3 and r4 are not in that
flood, so they never see underlay group `239.1.1.1`.

Every router uses the same wildcard-remote mGRE device (`gre1`). Overlay
multicast inject:

* r0/r1/r2: `map gre1,<local>,239.1.1.1` plus explicit unicast maps to
  r3 and r4, and `ujoin 239.1.1.1,eth0`
* r3/r4: explicit unicast `map` to every other router (no `ujoin`)

`gre1` is `layered` so overlay multicast that arrived on the tunnel is
not sent back out `gre1`. Sources on `eth1` still inject onto `gre1`.

Overlay unicast uses kernel `ip neigh`. Application multicast is sourced
on `h0` and received on `h1`..`h4`. While that flow is running, the test
captures 24 GRE packets leaving `r0` `eth0` (8 overlay pps × 3 inject
dests) and checks they are 8 to `239.1.1.1`, 8 to r3, and 8 to r4.

After that CF pass, the same overlay nrlsmf processes get runtime
`with-frr` and `elastic overlay` (maps, `layered gre1`, and grouping
stay). Two keeper cases:

* Keep multicast-underlay neighbor `h1` at 8 pps; stop `h2`/`h3`/`h4`
  (at most 1 pps).
* Then only unicast-only neighbor `h3` joins: `h3` at 8 pps; the other
  multicast neighbors (`h1`/`h2`) and the other unicast neighbor (`h4`)
  at most 1 pps.

FRR `pimd` serves IGMP on each router's host `eth1`.

## Run

From `tests/mutests` (requires root, `nrlsmf` on PATH):

```bash
sudo mutest mgre_mixed_unicast_mcast
```
