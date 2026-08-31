"""Example: point-to-point GRE + nrlsmf CF between paired routers.

Topology (shared with other tests in this directory):

         h0 -- r0 -- lan0 --\\
              r1 -- lan1 ---\\
                             u0   (underlay: routes ordinary IP between the LANs)
              r2 -- lan2 ---/
              r3 -- lan3 --/

What "point-to-point GRE" means here
-------------------------------------
A P2P GRE tunnel has exactly one local address and one remote address,
both fixed when the tunnel is created. There's no question of "which
peer does this packet go to" -- there's only ever one peer -- so unlike
the multipoint (mGRE) modes in the other files in this directory, no
peer-resolution mechanism (static table or NHRP) is needed at all.

This test builds two independent P2P tunnels out of the four available
routers -- r0<->r1 and r2<->r3 -- each riding over ordinary IP routing
through u0, the same way a real P2P GRE tunnel would ride over a WAN
hop. u0 itself is not GRE/nrlsmf-aware in this test; it's just an IP
router in the middle.

What this example covers
-------------------------
* Underlay: plain unicast IP reachability between the paired routers,
  routed through u0. No direct r0<->r1 (or r2<->r3) LAN is needed --
  the tunnel just needs IP reachability, however many hops that takes.
* Overlay: two independent point-to-point GRE tunnels, each with its
  own small /30 subnet so the two pairs' overlay addressing doesn't
  collide (r0/r1 use 172.16.0.0/30, r2/r3 use 172.16.1.0/30).
* nrlsmf on each router running classic flooding (`cf`) on its host
  LAN plus GRE iface. Iperf is sourced on h0 and received on h1 so
  SMF is both first hop onto and last hop off the r0<->r1 overlay.
  No `map` command is used: endpoint addressing is auto-discovered
  by nrlsmf from the kernel for an ordinary GRE interface.

See mutest_mgre_static.py (static NBMA mGRE), mutest_mgre_nhrp.py (NHRP-resolved
mGRE), mutest_mgre_mcast.py (multicast-underlay mGRE), and
mutest_gre_external.py (external/metadata GRE) for the multipoint
variants, where -- unlike here -- resolving "which peer" is the whole
point.
"""

from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import sys

sys.path.insert(0, str(script_dir()))
sys.path.insert(0, str(script_dir().parent))
from four_peer_hosts import cleanup_iperf
from four_peer_hosts import setup_host_lan
from four_peer_hosts import start_host_mcast_client
from four_peer_hosts import start_overlay_mcast_servers
from four_peer_hosts import wait_overlay_mcast_receivers
from smf_cli import check_common_show
from smf_cli import check_show_neighbors
from smf_cli import check_show_tunnel

# Two independent P2P GRE pairs, sharing the four-router underlay
# topology. Each pair gets its own /30 overlay subnet so the two
# tunnels' addressing can't be confused with each other.
PAIRS = [
    ("r0", "r1"),
    ("r2", "r3"),
]

# Underlay (physical, routed-through-u0) addresses -- one /24 per LAN.
UNDERLAY = {
    "r0": "10.0.0.2",
    "r1": "10.0.1.2",
    "r2": "10.0.2.2",
    "r3": "10.0.3.2",
}

# Overlay (GRE tunnel) addresses -- one /30 per pair:
#   r0 <-> r1 : 172.16.0.0/30 (r0=.1, r1=.2)
#   r2 <-> r3 : 172.16.1.0/30 (r2=.1, r3=.2)
OVERLAY = {
    "r0": "172.16.0.1",
    "r1": "172.16.0.2",
    "r2": "172.16.1.1",
    "r3": "172.16.1.2",
}

# Dedicated name: kernel fallback gre0 (remote any) will steal inbound
# GRE and the overlay subnet if we try to reuse it.
GRE_DEV = "gre1"
OVERLAY_MCAST = "239.0.0.1"


section("Disable offloads and wait for underlay addresses")

for name in UNDERLAY:
    step(name, "ethtool -K eth0 rx off tx off || true")
    wait_step(
        name,
        "ip -br addr show dev eth0",
        match=UNDERLAY[name],
        desc=f"{name} underlay address {UNDERLAY[name]}",
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

step("u0", "sysctl -w net.ipv4.ip_forward=1")

section("Underlay unicast reachability through u0")

for a, b in PAIRS:
    wait_step(
        a,
        f"ping -c1 -W2 {UNDERLAY[b]}",
        match="1 received",
        desc=f"{a} ping underlay {b} ({UNDERLAY[b]})",
        timeout=20,
    )
    wait_step(
        b,
        f"ping -c1 -W2 {UNDERLAY[a]}",
        match="1 received",
        desc=f"{b} ping underlay {a} ({UNDERLAY[a]})",
        timeout=20,
    )

section("Create point-to-point GRE tunnels")

for a, b in PAIRS:
    # Each side of the pair fixes both endpoints explicitly (local and
    # remote). There's no wildcard/"any" remote anywhere in a P2P
    # tunnel -- that's what distinguishes it from the mGRE cases.
    for local, remote in ((a, b), (b, a)):
        step(local, "ip addr flush dev gre0 2>/dev/null || true")
        step(local, "ip link set gre0 down 2>/dev/null || true")
        step(local, f"ip link del {GRE_DEV} 2>/dev/null || true")
        step(
            local,
            f"ip link add name {GRE_DEV} type gre "
            f"local {UNDERLAY[local]} remote {UNDERLAY[remote]} ttl 64",
        )
        step(local, f"ip addr add {OVERLAY[local]}/30 dev {GRE_DEV}")
        step(local, f"ip link set {GRE_DEV} multicast on")
        step(local, f"ip link set {GRE_DEV} up")
        wait_step(
            local,
            f"ip -br link show {GRE_DEV}",
            match="UP",
            desc=f"{local} {GRE_DEV} is UP",
        )

section("Overlay unicast across P2P GRE (before nrlsmf)")

for a, b in PAIRS:
    wait_step(
        a,
        f"ping -c1 -W3 {OVERLAY[b]}",
        match="1 received",
        desc=f"{a} ping overlay {b} ({OVERLAY[b]})",
        timeout=20,
    )
    wait_step(
        b,
        f"ping -c1 -W3 {OVERLAY[a]}",
        match="1 received",
        desc=f"{b} ping overlay {a} ({OVERLAY[a]})",
        timeout=20,
    )

section("Start nrlsmf classic flooding on P2P GRE")

for name in UNDERLAY:
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name}-p2p "
        f"add overlay,cf,eth1,{GRE_DEV} "
        f"layered {GRE_DEV} "
        "&> nrlsmf-gre-p2p.log &",
    )
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}-p2p"',
        match=f"smf-{name}-p2p",
        desc=f"{name} nrlsmf running on {GRE_DEV}",
        timeout=20,
    )
    wait_step(
        name,
        'grep "regular group" nrlsmf-gre-p2p.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("nrlsmf --cli show tunnel / neighbors (json, kernel-learned P2P)")

# No map: Local/Remote come from the GRE device. P2P GRE is NOARP, so
# overlay pings do not install Neighbor IP; the kernel remote is listed.
for a, b in PAIRS:
    inst_a = f"smf-{a}-p2p"
    inst_b = f"smf-{b}-p2p"
    check_common_show(a, inst_a, group_name="overlay", ifaces=("eth1", GRE_DEV))
    check_show_tunnel(
        a, inst_a, GRE_DEV,
        local=UNDERLAY[a], remotes=[UNDERLAY[b]], overlay_ip=OVERLAY[a], want_c=False,
    )
    check_show_neighbors(
        a, inst_a, GRE_DEV,
        remotes=[UNDERLAY[b]], min_count=1,
    )
    check_show_tunnel(
        b, inst_b, GRE_DEV,
        local=UNDERLAY[b], remotes=[UNDERLAY[a]], overlay_ip=OVERLAY[b], want_c=False,
    )
    check_show_neighbors(
        b, inst_b, GRE_DEV,
        remotes=[UNDERLAY[a]], min_count=1,
    )

# h0 is the source (off r0); h1 (off r1) is the receiver for this pair.
# r2<->r3 stays a unicast-only overlay check.
section("[P2P] Overlay multicast: h0 -> SMF -> h1")

RECEIVERS = ("h1",)
start_overlay_mcast_servers(step, RECEIVERS, OVERLAY_MCAST)
start_host_mcast_client(step, wait_step, OVERLAY_MCAST)
wait_overlay_mcast_receivers(wait_step, RECEIVERS)

section("Cleanup")

cleanup_iperf(step, RECEIVERS)
for name in UNDERLAY:
    step(name, "pkill nrlsmf || true")
    wait_step(
        name,
        "pgrep -af nrlsmf || true",
        match="",
        desc=f"{name} nrlsmf stopped",
        timeout=15,
    )

test_step(True, "P2P GRE paired mutest completed")
