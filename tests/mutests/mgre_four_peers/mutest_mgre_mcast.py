"""Example: mGRE with multicast underlay remote + nrlsmf CF among four routers.

Topology (shared with other tests in this directory):

         h0 -- r0 -- lan0 --\\
         h1 -- r1 -- lan1 ---\\
                              u0   (underlay: relays multicast between the LANs)
         h2 -- r2 -- lan2 ---/
         h3 -- r3 -- lan3 --/

What "multicast-underlay mGRE" means here
-------------------------------------------
The other two mGRE modes in this directory (mutest_mgre_static.py and
mutest_mgre_nhrp.py) both solve "which peer does this packet go to" by
building a table -- static or dynamic -- that resolves each peer's
overlay address to a specific underlay unicast address, then sending
one unicast-encapsulated copy per peer. This mode does something
different: instead of a table, the tunnel's *remote* address is
configured as an IP multicast group address. A single encapsulated
transmission is sent once, to that group, and the underlay network's
own multicast routing fans it out to every router that has joined the
group -- no per-peer table, no unicast replication at the sender.

There's no real PIM multicast router available in this lab topology, so
u0 stands in for "a multicast-capable underlay" by running nrlsmf
itself in a pure dataplane role: `nrlsmf rmerge` across all four of its
LAN-facing interfaces, which floods any multicast packet arriving on
one of u0's interfaces out all the others. This is u0's *only* job in
this test -- it does not participate in the GRE/mGRE overlay itself,
and this nrlsmf instance on u0 is completely independent of the
per-router overlay nrlsmf instances started later. A real deployment
would use actual PIM multicast routing here instead of nrlsmf; this
substitution exists purely to make the test self-contained without
requiring a separate multicast routing daemon.

What this example covers
-------------------------
* Underlay dataplane on u0: `nrlsmf rmerge` across eth0..eth3, standing
  in for real underlay multicast routing (see above).
* Overlay: GRE tunnels on r0..r3 with a multicast remote address
  (mgre0), so one underlay multicast group reaches all four routers
  symmetrically -- no hub, no per-peer replication.
* Overlay nrlsmf: classic flooding (`cf`) on each router's host LAN
  plus mgre0, with `ujoin` on each router's underlay eth0.
* Overlay multicast from host h0, received at h1/h2/h3.

See mutest_gre_p2p.py (point-to-point), mutest_mgre_static.py (static NBMA
mGRE), mutest_mgre_nhrp.py (NHRP-resolved mGRE), and
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

ROUTERS = {
    "r0": {"underlay": "10.0.0.2", "overlay": "172.16.0.1"},
    "r1": {"underlay": "10.0.1.2", "overlay": "172.16.0.2"},
    "r2": {"underlay": "10.0.2.2", "overlay": "172.16.0.3"},
    "r3": {"underlay": "10.0.3.2", "overlay": "172.16.0.4"},
}

UNDERLAY_MCAST = "239.1.1.1"
OVERLAY_MCAST = "239.0.0.1"
# Dedicated name: kernel fallback gre0 (remote any) steals the overlay
# subnet if addressed; see tunnel-setup comments below.
GRE_DEV = "mgre0"
U0_IFACES = "eth0,eth1,eth2,eth3"


section("Disable offloads and wait for underlay addresses")

step("u0", "sysctl -w net.ipv4.ip_forward=1")
step("u0", "sysctl -w net.ipv4.conf.all.rp_filter=0")
step("u0", "sysctl -w net.ipv4.conf.default.rp_filter=0")
step("u0", "sysctl -w net.ipv4.conf.all.send_redirects=0")

for name, cfg in ROUTERS.items():
    step(name, "ethtool -K eth0 rx off tx off || true")
    step(name, "sysctl -w net.ipv4.conf.all.rp_filter=0")
    step(name, "sysctl -w net.ipv4.conf.eth0.rp_filter=0")
    step(name, "sysctl -w net.ipv4.conf.all.send_redirects=0")
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
    step("u0", f"sysctl -w net.ipv4.conf.{ifname}.rp_filter=0")
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

section("Underlay multicast relay on u0 (stand-in for real PIM routing)")

# u0 is not part of the GRE/mGRE overlay -- this nrlsmf instance only
# floods multicast between u0's four LAN interfaces, so GRE packets
# addressed to UNDERLAY_MCAST reach every router. Stand-in for real
# underlay multicast routing (e.g. PIM).
step(
    "u0",
    "nrlsmf debug 4 "
    "instance smf-u0-underlay "
    f"rmerge {U0_IFACES} "
    "&> nrlsmf-u0-underlay.log &",
)
wait_step(
    "u0",
    'pgrep -af "nrlsmf.*instance smf-u0-underlay"',
    match="smf-u0-underlay",
    desc="u0 underlay nrlsmf running",
    timeout=20,
)
wait_step(
    "u0",
    'grep "regular group" nrlsmf-u0-underlay.log',
    match="merge",
    desc="u0 nrlsmf log shows merge group",
    timeout=20,
)

section("Create mGRE tunnels (multicast underlay remote)")

for name, cfg in ROUTERS.items():
    # Linux always creates a fallback gre0 (remote any / local any). If the
    # overlay /24 lands on that device, routes prefer it over mgre0 and the
    # multicast-remote tunnel never carries traffic. Flush and down gre0 so
    # it cannot own the overlay subnet; delete any leftover mgre0 from a
    # prior run before recreating it. (Hence GRE_DEV=mgre0 instead of trying
    # to reconfigure the kernel's built-in gre0.)
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    # This is the defining difference from the other mGRE modes: remote
    # is a multicast group address, not a unicast peer or 0.0.0.0.
    # Linux derives ikey/okey from the multicast remote; peers share
    # that group.
    step(
        name,
        f"ip tunnel add {GRE_DEV} mode gre "
        f"local {cfg['underlay']} remote {UNDERLAY_MCAST} ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} multicast on")
    step(name, f"ip link set {GRE_DEV} up")
    wait_step(
        name,
        f"ip -br link show {GRE_DEV}",
        match="UP",
        desc=f"{name} {GRE_DEV} is UP",
    )
    wait_step(
        name,
        f"ip -d link show {GRE_DEV}",
        match=UNDERLAY_MCAST,
        desc=f"{name} {GRE_DEV} remote is {UNDERLAY_MCAST}",
    )

section("Overlay unicast across mGRE (before overlay nrlsmf)")

for src, scfg in ROUTERS.items():
    for dst, dcfg in ROUTERS.items():
        if src == dst:
            continue
        wait_step(
            src,
            f"ping -c1 -W3 -I {GRE_DEV} {dcfg['overlay']}",
            match="1 received",
            desc=f"{src} ping overlay {dst} ({dcfg['overlay']})",
            timeout=20,
        )

section("Start overlay nrlsmf classic flooding on mGRE (ujoin required)")

for name in ROUTERS:
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name}-mcast "
        f"add overlay,cf,eth1,{GRE_DEV} "
        f"layered {GRE_DEV} "
        f"ujoin {UNDERLAY_MCAST},eth0 "
        "&> nrlsmf-mgre-mcast.log &",
    )
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}-mcast"',
        match=f"smf-{name}-mcast",
        desc=f"{name} nrlsmf running on {GRE_DEV}",
        timeout=20,
    )
    wait_step(
        name,
        'grep "regular group" nrlsmf-mgre-mcast.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("nrlsmf --cli show tunnel / neighbors (json, multicast-underlay remote)")

check_common_show("u0", "smf-u0-underlay", group_name="merge")
for name, cfg in ROUTERS.items():
    inst = f"smf-{name}-mcast"
    check_common_show(name, inst, group_name="overlay", ifaces=("eth1", GRE_DEV))
    check_show_tunnel(
        name, inst, GRE_DEV,
        local=cfg["underlay"],
        remotes=[UNDERLAY_MCAST],
        overlay_ip=cfg["overlay"],
        want_c=False,
    )
    # Device remote is the underlay group. NOARP, so no per-peer overlay neigh.
    check_show_neighbors(
        name, inst, GRE_DEV,
        remotes=[UNDERLAY_MCAST],
        min_count=1,
    )

section("[Mcast] Overlay multicast: h0 -> SMF -> h1/h2/h3")

step(
    "r0",
    f"tcpdump -l -n -i eth0 'proto gre or host {UNDERLAY_MCAST}' "
    "> tcpdump-r0-eth0.log 2>&1 &",
)
step(
    "r0",
    f"tcpdump -l -n -i {GRE_DEV} 'host {OVERLAY_MCAST}' "
    "> tcpdump-r0-mgre0.log 2>&1 &",
)
step(
    "r1",
    f"tcpdump -l -n -i eth0 'proto gre or host {UNDERLAY_MCAST}' "
    "> tcpdump-r1-eth0.log 2>&1 &",
)
step(
    "r1",
    f"tcpdump -l -n -i {GRE_DEV} 'host {OVERLAY_MCAST}' "
    "> tcpdump-r1-mgre0.log 2>&1 &",
)
step("r0", "sleep 1")

RECEIVERS = RECV_HOSTS
start_overlay_mcast_servers(step, RECEIVERS, OVERLAY_MCAST)
start_host_mcast_client(step, wait_step, OVERLAY_MCAST)
wait_overlay_mcast_receivers(wait_step, RECEIVERS)

step("r0", "pkill tcpdump || true")
step("r1", "pkill tcpdump || true")
step("r0", "echo '=== r0 eth0 ==='; cat tcpdump-r0-eth0.log || true")
step("r0", "echo '=== r0 mgre0 ==='; cat tcpdump-r0-mgre0.log || true")
step("r1", "echo '=== r1 eth0 ==='; cat tcpdump-r1-eth0.log || true")
step("r1", "echo '=== r1 mgre0 ==='; cat tcpdump-r1-mgre0.log || true")

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

step("u0", "pkill nrlsmf || true")
test_step(True, "mGRE underlay-mcast four-router mutest completed")
