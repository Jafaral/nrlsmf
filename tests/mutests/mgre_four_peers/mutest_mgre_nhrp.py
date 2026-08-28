"""Example: NHRP-resolved mGRE (dynamic hub-and-spoke) + nrlsmf CF.

Topology (shared with other tests in this directory):

         h0 -- r0 -- lan0 --\\
              r1 -- lan1 ---\\
                             u0   (underlay: routes ordinary IP between the LANs)
              r2 -- lan2 ---/
              r3 -- lan3 --/   <-- also the NHRP Server (NHS) / hub

Why the NHS runs on r3, not on u0
------------------------------------
u0 represents the underlay/WAN -- the network that gets you from one
router to another, but that the operator running the GRE/mGRE overlay
generally does *not* control (think: the public Internet, a carrier
MPLS core, or a satellite/cellular provider's network). It would be a
mistake to put the NHRP Server there: the NHS is part of the overlay's
own control plane, and needs to run somewhere the operator actually
owns and trusts.

So in this test, r3 -- one of the operator's own four routers, exactly
like r0..r2 -- takes on the hub/NHS role in addition to being a normal
overlay participant. r0..r2 are NHRP clients ("spokes") that register
their real underlay address with r3 and are, from that point on,
reachable by their overlay tunnel address without any manual mapping.
u0 is not touched by NHRP configuration at all; it just routes plain
IP packets between the four LANs, the same as in every other test in
this directory.

What "NHRP-resolved mGRE" means here
---------------------------------------
This is the third way of answering "which peer does this packet go to"
on a multipoint GRE interface, and it's the one used by dynamic
hub-and-spoke overlays such as Cisco-style DMVPN. Instead of a
hand-built table (mutest_mgre_static.py), the overlay-to-underlay address
table is built and kept up to date automatically by the Next Hop
Resolution Protocol (NHRP, RFC 2332), via FRR's `nhrpd` daemon -- an
external control-plane programming the tunnel. nrlsmf does not list
peers; it uses `map …,dynamic` and reads the kernel `ip neigh` table
nhrpd installed. Overlay unicast looks like static NBMA. Overlay
multicast does not: a spoke only has the NHS in neigh, so the hub's
nrlsmf is the replicator.

What this example covers
-------------------------
* Underlay: unicast IP between all four routers, routed through u0 --
  identical to the other tests. r3's own ordinary underlay address
  (its `eth0` on lan3) is used directly as its NBMA identity; no
  loopback or special addressing is needed on r3 or on u0.
* Overlay: a single multipoint GRE interface (gre1) where overlay
  unicast peer resolution is handled by `nhrpd`. Overlay tunnel
  addresses (10.100.0.0/24) are a separate range from the underlay
  addresses (10.0.0.0/24-10.0.3.0/24).
* nrlsmf classic flooding (`cf`) on each router's host LAN plus gre1.
  `map gre1,<local>,dynamic` is the only inject config: nrlsmf learns
  GRE dests from kernel `ip neigh` that nhrpd installed. That is the
  point of this test -- nhrpd is an external tool programming the
  tunnel, not nrlsmf listing peers itself.
* Hub-and-spoke (DMVPN Phase 1), not spoke-to-spoke shortcuts. A spoke's
  neigh table is the NHS; the hub's neigh table is every registered
  spoke. Overlay multicast from h0 therefore goes r0 -> r3, and r3's
  nrlsmf replicates to r1/r2. Spokes `layered gre1` so they do not
  flood back out the tunnel; the hub is *not* layered, because it is
  the overlay replicator. (Shortcuts need NFLOG/iptables and would not
  fire on nrlsmf CF traffic anyway.)

See mutest_gre_p2p.py (point-to-point), mutest_mgre_static.py (static NBMA
mGRE), mutest_mgre_mcast.py (multicast-underlay mGRE), and
mutest_gre_external.py (external/metadata GRE) for the other GRE
tunnel modes.
"""

from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import sys

sys.path.insert(0, str(script_dir()))
sys.path.insert(0, str(script_dir().parent))
from four_peer_hosts import RECV_HOSTS
from four_peer_hosts import cleanup_iperf
from four_peer_hosts import setup_host_lan
from four_peer_hosts import start_host_mcast_client
from four_peer_hosts import start_overlay_mcast_servers
from four_peer_hosts import wait_overlay_mcast_receivers
from smf_cli import check_common_show
from smf_cli import check_show_neighbors
from smf_cli import check_show_tunnel

# Underlay LAN addressing (matches */etc.frr/frr.conf). Overlay/tunnel
# addresses are deliberately drawn from a separate 10.100.0.0/24 range
# so they can never collide with underlay addresses. r3 is the hub/NHS;
# r0..r2 are spokes.
HUB = "r3"
SPOKES = ("r0", "r1", "r2")

ROUTERS = {
    "r0": {"underlay": "10.0.0.2", "overlay": "10.100.0.2"},
    "r1": {"underlay": "10.0.1.2", "overlay": "10.100.0.3"},
    "r2": {"underlay": "10.0.2.2", "overlay": "10.100.0.4"},
    "r3": {"underlay": "10.0.3.2", "overlay": "10.100.0.1"},  # hub/NHS
}

GRE_DEV = "gre1"
NHRP_NETWORK_ID = "1"
GRE_KEY = "42"
OVERLAY_MCAST = "239.0.0.1"


section("Disable offloads and wait for underlay addresses")

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

step("u0", "sysctl -w net.ipv4.ip_forward=1")

section("Underlay unicast reachability (spokes to hub, through u0)")

# u0 has no NHRP role and no special configuration here -- it's just
# plain IP transit, exactly as in every other test in this directory.
for name in SPOKES:
    wait_step(
        name,
        f"ping -c1 -W2 {ROUTERS[HUB]['underlay']}",
        match="1 received",
        desc=f"{name} ping hub underlay {HUB} ({ROUTERS[HUB]['underlay']})",
        timeout=20,
    )

section("Create multipoint GRE interface on the hub and each spoke")

# Kernel fallback gre0 (remote any) will steal inbound GRE if left up.
step(HUB, "ip addr flush dev gre0 2>/dev/null || true")
step(HUB, "ip link set gre0 down 2>/dev/null || true")
step(HUB, f"ip link del {GRE_DEV} 2>/dev/null || true")
step(
    HUB,
    f"ip tunnel add {GRE_DEV} mode gre "
    f"local {ROUTERS[HUB]['underlay']} key {GRE_KEY} ttl 64",
)
step(HUB, f"ip addr add {ROUTERS[HUB]['overlay']}/32 dev {GRE_DEV}")
step(HUB, f"ip link set {GRE_DEV} multicast on")
step(HUB, f"ip link set {GRE_DEV} up")
wait_step(
    HUB,
    f"ip -br link show {GRE_DEV}",
    match="UP",
    desc=f"{HUB} {GRE_DEV} is UP",
)

for name in SPOKES:
    cfg = ROUTERS[name]
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    step(
        name,
        f"ip tunnel add {GRE_DEV} mode gre "
        f"local {cfg['underlay']} key {GRE_KEY} ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/32 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} multicast on")
    step(name, f"ip link set {GRE_DEV} up")
    wait_step(
        name,
        f"ip -br link show {GRE_DEV}",
        match="UP",
        desc=f"{name} {GRE_DEV} is UP",
    )

section("Configure FRR nhrpd: hub (NHS) on r3")

wait_step(
    HUB,
    "pgrep -af nhrpd",
    match="nhrpd",
    desc=f"{HUB} nhrpd is running",
    timeout=20,
)

step(
    HUB,
    f"vtysh -c 'configure terminal' "
    f"-c 'interface {GRE_DEV}' "
    f"-c 'ip nhrp network-id {NHRP_NETWORK_ID}' "
    f"-c 'ip nhrp registration no-unique'",
)

section("Configure FRR nhrpd: spokes (NHC, register with r3)")

for name in SPOKES:
    wait_step(
        name,
        "pgrep -af nhrpd",
        match="nhrpd",
        desc=f"{name} nhrpd is running",
        timeout=20,
    )
    step(
        name,
        f"vtysh -c 'configure terminal' "
        f"-c 'interface {GRE_DEV}' "
        f"-c 'ip nhrp network-id {NHRP_NETWORK_ID}' "
        f"-c 'ip nhrp nhs {ROUTERS[HUB]['overlay']} "
        f"nbma {ROUTERS[HUB]['underlay']}' "
        f"-c 'ip nhrp registration no-unique'",
    )

section("Wait for spoke registration with the hub")

# Registration is sent shortly after the nhs is configured; give it
# room to converge rather than asserting on internal timer behavior.
for name in SPOKES:
    cfg = ROUTERS[name]
    wait_step(
        HUB,
        "vtysh -c 'show ip nhrp cache'",
        match=cfg["overlay"],
        desc=f"{HUB} NHRP cache shows {name} registered ({cfg['overlay']})",
        timeout=60,
    )

for name in SPOKES:
    wait_step(
        name,
        "vtysh -c 'show ip nhrp nhs'",
        match=ROUTERS[HUB]["overlay"],
        desc=f"{name} NHRP shows hub NHS {ROUTERS[HUB]['overlay']}",
        timeout=30,
    )

section("Overlay unicast, hub <-> spokes, resolved via NHRP (before nrlsmf)")

for name in SPOKES:
    cfg = ROUTERS[name]
    wait_step(
        name,
        f"ping -c1 -W3 {ROUTERS[HUB]['overlay']}",
        match="1 received",
        desc=f"{name} ping hub overlay {ROUTERS[HUB]['overlay']}",
        timeout=20,
    )
    wait_step(
        HUB,
        f"ping -c1 -W3 {cfg['overlay']}",
        match="1 received",
        desc=f"{HUB} ping {name} overlay ({cfg['overlay']})",
        timeout=20,
    )

section("Kernel neigh that nhrpd programmed (nrlsmf map dynamic source)")

# Spoke: NHS only. Hub: every registered spoke. Overlay pings above
# make sure those entries are in the kernel, not only in nhrpd's cache.
for name in SPOKES:
    wait_step(
        name,
        f"ip neigh show dev {GRE_DEV}",
        match=ROUTERS[HUB]["overlay"],
        desc=f"{name} gre1 neigh has hub {ROUTERS[HUB]['overlay']}",
        timeout=20,
    )
for name in SPOKES:
    wait_step(
        HUB,
        f"ip neigh show dev {GRE_DEV}",
        match=ROUTERS[name]["overlay"],
        desc=f"{HUB} gre1 neigh has {name} {ROUTERS[name]['overlay']}",
        timeout=20,
    )

section("Start nrlsmf (map dynamic from nhrpd neigh; hub replicates)")

# Spokes are layered so a packet received on gre1 is not flooded back
# out of it. The hub is not: it is the overlay-mcast replicator, using
# the spoke dests nhrpd installed.
for name, cfg in ROUTERS.items():
    layered = f"layered {GRE_DEV} " if name in SPOKES else ""
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name} "
        f"add overlay,cf,eth1,{GRE_DEV} "
        f"{layered}"
        f"map {GRE_DEV},{cfg['underlay']},dynamic "
        "&> nrlsmf-mgre-nhrp.log &",
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
        'grep "regular group" nrlsmf-mgre-nhrp.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("nrlsmf --cli show tunnel / neighbors (json, NHRP map dynamic)")

# Spokes see the NHS; the hub sees every registered spoke. Overlay Neighbor IP
# comes from nhrpd's kernel neigh; C is not required (dynamic, not explicit map).
for name in SPOKES:
    inst = f"smf-{name}"
    check_common_show(name, inst, group_name="overlay", ifaces=("eth1", GRE_DEV))
    check_show_tunnel(
        name, inst, GRE_DEV,
        local=ROUTERS[name]["underlay"],
        remotes=[ROUTERS[HUB]["underlay"]],
        overlay_ip=ROUTERS[name]["overlay"],
    )
    check_show_neighbors(
        name, inst, GRE_DEV,
        remotes=[ROUTERS[HUB]["underlay"]],
        neighbor_ips=[ROUTERS[HUB]["overlay"]],
        min_count=1,
    )

hub_inst = f"smf-{HUB}"
check_common_show(HUB, hub_inst, group_name="overlay", ifaces=("eth1", GRE_DEV))
check_show_tunnel(
    HUB, hub_inst, GRE_DEV,
    local=ROUTERS[HUB]["underlay"],
    remotes=[ROUTERS[s]["underlay"] for s in SPOKES],
    overlay_ip=ROUTERS[HUB]["overlay"],
)
check_show_neighbors(
    HUB, hub_inst, GRE_DEV,
    remotes=[ROUTERS[s]["underlay"] for s in SPOKES],
    neighbor_ips=[ROUTERS[s]["overlay"] for s in SPOKES],
    min_count=3,
)

section("[NHRP] Overlay multicast: h0 -> r0 -> hub -> h1/h2/h3")

RECEIVERS = RECV_HOSTS
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

test_step(True, "NHRP-resolved mGRE four-router mutest completed")
