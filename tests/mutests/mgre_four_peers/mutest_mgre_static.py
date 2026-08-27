"""Example: multipoint GRE (static NBMA) + nrlsmf CF among four routers.

Topology (shared with other tests in this directory):

         h0 -- r0 -- lan0 --\\
              r1 -- lan1 ---\\
                             u0   (underlay: routes ordinary IP between the LANs)
              r2 -- lan2 ---/
              r3 -- lan3 --/

What "static NBMA mGRE" means here
------------------------------------
A single mGRE interface (kernel `remote 0.0.0.0`) can represent many
remote peers instead of just one, but GRE itself has no way to say
"which peer does this packet go to" -- something else has to answer
that. Here, that something is a plain, hand-built table: each router's
kernel neighbor table (`ip neigh`) maps every other router's *overlay*
tunnel address to that router's *underlay* address, the same way an
ARP table maps an IP to a MAC address on an Ethernet LAN. It's manual
and doesn't scale to large or changing meshes, but it needs no extra
daemon or protocol -- just entries you set once.

(See mutest_mgre_nhrp.py for the alternative where this same table is
filled in dynamically by a routing daemon instead of by hand -- at the
kernel level the two are the same mechanism, just maintained
differently.)

What this example covers
-------------------------
* Underlay: unicast IP between all four routers, routed through u0.
* Overlay: one shared multipoint GRE interface (gre1) on each router,
  all in the same 172.16.0.0/24 overlay subnet.
* Two peer tables, because overlay unicast and overlay multicast are
  resolved differently:
    - Kernel `ip neigh`: overlay unicast address -> underlay address.
      Used by the GRE driver for ordinary overlay ping/unicast.
    - nrlsmf overlay-multicast inject dests, two passes:
        1. Explicit `map gre1,<local>,<peer>` for every other router.
        2. Stop nrlsmf on sender r0 and restart it with
           `map gre1,<local>,dynamic` so the same dests are learned
           from kernel `ip neigh`. Receivers keep their explicit maps.
      `0.0.0.0` remains the kernel wildcard remote (not a send dest).
* nrlsmf classic flooding (`cf`) on each router's host LAN plus gre1.
  Iperf is sourced on h0 and received on h1/h2/h3.

See mutest_gre_p2p.py (point-to-point), mutest_mgre_nhrp.py (NHRP-
resolved mGRE), mutest_mgre_mcast.py (multicast-underlay mGRE), and
mutest_gre_external.py (external/metadata GRE, resolved via routes
instead of this file's neighbor table) for the other GRE tunnel modes.
"""

from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import sys

sys.path.insert(0, str(script_dir()))
from four_peer_hosts import RECV_HOSTS
from four_peer_hosts import cleanup_iperf
from four_peer_hosts import setup_host_lan
from four_peer_hosts import start_host_mcast_client
from four_peer_hosts import start_overlay_mcast_servers
from four_peer_hosts import wait_overlay_mcast_receivers

# Underlay LAN addressing (matches */etc.frr/frr.conf)
ROUTERS = {
    "r0": {"underlay": "10.0.0.2", "overlay": "172.16.0.1"},
    "r1": {"underlay": "10.0.1.2", "overlay": "172.16.0.2"},
    "r2": {"underlay": "10.0.2.2", "overlay": "172.16.0.3"},
    "r3": {"underlay": "10.0.3.2", "overlay": "172.16.0.4"},
}

OVERLAY_MCAST = "239.0.0.1"
# Dedicated name: kernel fallback gre0 (remote any) will steal inbound
# GRE and the overlay subnet if we try to reuse it.
GRE_DEV = "gre1"


section("Disable offloads and wait for underlay addresses")

step("u0", "sysctl -w net.ipv4.ip_forward=1")

for name, cfg in ROUTERS.items():
    step(name, "ethtool -K eth0 rx off tx off || true")
    wait_step(
        name,
        "ip -br addr show dev eth0",
        match=cfg["underlay"],
        desc=f"{name} underlay address {cfg['underlay']}",
        timeout=30,
    )

for ifname, addr in (
    ("eth0", "10.0.0.1"),
    ("eth1", "10.0.1.1"),
    ("eth2", "10.0.2.1"),
    ("eth3", "10.0.3.1"),
):
    step("u0", f"ethtool -K {ifname} rx off tx off || true")
    wait_step(
        "u0",
        f"ip -br addr show dev {ifname}",
        match=addr,
        desc=f"u0 {ifname} address {addr}",
        timeout=30,
    )

setup_host_lan(step, wait_step)

section("Underlay unicast reachability through u0")

for src in ROUTERS:
    for dst, dcfg in ROUTERS.items():
        if src == dst:
            continue
        wait_step(
            src,
            f"ping -c1 -W2 {dcfg['underlay']}",
            match="1 received",
            desc=f"{src} ping underlay {dst} ({dcfg['underlay']})",
            timeout=20,
        )

section("Create multipoint GRE tunnels with static NBMA neighbors")

for name, cfg in ROUTERS.items():
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    # remote 0.0.0.0: this is what makes the interface multipoint
    # rather than point-to-point -- there's no single fixed peer.
    step(
        name,
        f"ip link add name {GRE_DEV} type gre "
        f"local {cfg['underlay']} remote 0.0.0.0 ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} multicast on")
    step(name, f"ip link set {GRE_DEV} up")
    # Static NBMA table: map every other router's overlay address to
    # its underlay address, by hand. This is the "static" half of
    # "static NBMA mGRE" -- an NHRP daemon would maintain these same
    # kinds of entries dynamically instead (see mutest_mgre_nhrp.py).
    for other, ocfg in ROUTERS.items():
        if other == name:
            continue
        step(
            name,
            f"ip neigh replace {ocfg['overlay']} lladdr {ocfg['underlay']} "
            f"nud permanent dev {GRE_DEV}",
        )
    wait_step(
        name,
        f"ip -br link show {GRE_DEV}",
        match="UP",
        desc=f"{name} {GRE_DEV} is UP",
    )

section("Overlay unicast across mGRE (before nrlsmf)")

for src, scfg in ROUTERS.items():
    for dst, dcfg in ROUTERS.items():
        if src == dst:
            continue
        wait_step(
            src,
            f"ping -c1 -W3 -I {scfg['overlay']} {dcfg['overlay']}",
            match="1 received",
            desc=f"{src} ping overlay {dst} ({dcfg['overlay']})",
            timeout=20,
        )

section("Start nrlsmf classic flooding on mGRE")

# Overlay mcast inject dests: one map per other router's underlay address.
for name, cfg in ROUTERS.items():
    maps = " ".join(
        f"map {GRE_DEV},{cfg['underlay']},{ocfg['underlay']}"
        for other, ocfg in ROUTERS.items()
        if other != name
    )
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name} "
        f"add overlay,cf,eth1,{GRE_DEV} "
        f"layered {GRE_DEV} "
        f"{maps} "
        "&> nrlsmf-mgre.log &",
    )
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}"',
        match=f"smf-{name}",
        desc=f"{name} nrlsmf running on {GRE_DEV}",
        timeout=20,
    )
    wait_step(
        name,
        'grep "regular group" nrlsmf-mgre.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("[Static] Overlay multicast: h0 -> SMF -> h1/h2/h3 (explicit map)")

RECEIVERS = RECV_HOSTS
start_overlay_mcast_servers(step, RECEIVERS, OVERLAY_MCAST)
start_host_mcast_client(step, wait_step, OVERLAY_MCAST)
wait_overlay_mcast_receivers(wait_step, RECEIVERS)

section("[Static] Overlay multicast: h0 -> SMF -> h1/h2/h3 (r0 map dynamic)")

cleanup_iperf(step, RECEIVERS)
step("r0", "pkill nrlsmf || true")
wait_step(
    "r0",
    "pgrep -af nrlsmf || true",
    match="",
    desc="r0 nrlsmf stopped",
    timeout=15,
)
step(
    "r0",
    "nrlsmf debug 4 "
    "instance smf-r0 "
    f"add overlay,cf,eth1,{GRE_DEV} "
    f"layered {GRE_DEV} "
    f"map {GRE_DEV},{ROUTERS['r0']['underlay']},dynamic "
    "&> nrlsmf-mgre-dynamic.log &",
)
wait_step(
    "r0",
    'pgrep -af "nrlsmf.*instance smf-r0"',
    match="smf-r0",
    desc="r0 nrlsmf running on gre1 (map dynamic)",
    timeout=20,
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-mgre-dynamic.log',
    match="overlay",
    desc="r0 nrlsmf log shows overlay group",
    timeout=20,
)
start_overlay_mcast_servers(step, RECEIVERS, OVERLAY_MCAST)
start_host_mcast_client(step, wait_step, OVERLAY_MCAST)
wait_overlay_mcast_receivers(wait_step, RECEIVERS)

section("Cleanup")

cleanup_iperf(step, RECEIVERS)
for name in ROUTERS:
    step(name, "pkill nrlsmf || true")
    wait_step(
        name,
        "pgrep -af nrlsmf || true",
        match="",
        desc=f"{name} nrlsmf stopped",
        timeout=15,
    )

test_step(True, "mGRE NBMA four-router mutest completed")
