"""Example: mGRE with multicast underlay remote + nrlsmf CF among four peers.

Topology (shared with other tests in this directory):

  p1 -- lan1 --\
  p2 -- lan2 ---\
                 r0   (hub; underlay unicast + nrlsmf rmerge dataplane)
  p3 -- lan3 ---/
  p4 -- lan4 --/

What this example covers
------------------------
* Underlay: unicast routing through r0, plus nrlsmf on r0 as a pure
  dataplane gateway (rmerge across eth0..eth3) so the GRE encapsulation
  group is flooded between the LANs. That instance is independent of the
  overlay nrlsmf instances on the peers.
* Overlay: GRE tunnels with multicast remote (one underlay group reaches
  all peers). Peer nrlsmf classic flooding on mgre0 with map/ujoin.
* Overlay multicast: iperf from p1 to p2/p3/p4 over the GRE overlay.

See mutest_mgre.py for the NBMA (unicast underlay) multipoint GRE example.
"""

from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

PEERS = {
    "p1": {"underlay": "10.0.1.2", "overlay": "172.16.0.1"},
    "p2": {"underlay": "10.0.2.2", "overlay": "172.16.0.2"},
    "p3": {"underlay": "10.0.3.2", "overlay": "172.16.0.3"},
    "p4": {"underlay": "10.0.4.2", "overlay": "172.16.0.4"},
}

UNDERLAY_MCAST = "239.1.1.1"
OVERLAY_MCAST = "239.0.0.1"
# Dedicated name: kernel fallback gre0 (remote any) steals the overlay
# subnet if addressed; see tunnel-setup comments below.
GRE_DEV = "mgre0"
R0_IFACES = "eth0,eth1,eth2,eth3"


def peer_names():
    return list(PEERS.keys())


section("Disable offloads and wait for underlay addresses")

step("r0", "sysctl -w net.ipv4.ip_forward=1")
step("r0", "sysctl -w net.ipv4.conf.all.rp_filter=0")
step("r0", "sysctl -w net.ipv4.conf.default.rp_filter=0")
step("r0", "sysctl -w net.ipv4.conf.all.send_redirects=0")

for name, cfg in PEERS.items():
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
    ("eth0", "10.0.1.1"),
    ("eth1", "10.0.2.1"),
    ("eth2", "10.0.3.1"),
    ("eth3", "10.0.4.1"),
):
    step("r0", f"ethtool -K {ifname} rx off tx off || true")
    step("r0", f"sysctl -w net.ipv4.conf.{ifname}.rp_filter=0")
    wait_step(
        "r0",
        f"ip -br addr show dev {ifname}",
        match=addr,
        desc=f"r0 {ifname} address {addr}",
        timeout=30,
    )

section("Underlay unicast reachability through hub router")

for src in peer_names():
    for dst, dcfg in PEERS.items():
        if src == dst:
            continue
        wait_step(
            src,
            f"ping -c1 -W2 {dcfg['underlay']}",
            match="1 received",
            desc=f"{src} ping underlay {dst} ({dcfg['underlay']})",
            timeout=20,
        )

section("Underlay mcast dataplane on r0 (nrlsmf rmerge)")

# Independent of peer overlay instances: flood multicast among the hub LANs
# so GRE packets destined to UNDERLAY_MCAST reach every peer.
step(
    "r0",
    "nrlsmf debug 4 "
    "instance smf-r0-underlay "
    f"rmerge {R0_IFACES} "
    "&> nrlsmf-r0-underlay.log &",
)
wait_step(
    "r0",
    'pgrep -af "nrlsmf.*instance smf-r0-underlay"',
    match="smf-r0-underlay",
    desc="r0 underlay nrlsmf running",
    timeout=20,
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-r0-underlay.log',
    match="merge",
    desc="r0 nrlsmf log shows merge group",
    timeout=20,
)

section("Create mGRE tunnels (multicast underlay remote)")

for name, cfg in PEERS.items():
    # Linux always creates a fallback gre0 (remote any / local any). If the
    # overlay /24 lands on that device, routes prefer it over mgre0 and the
    # multicast-remote tunnel never carries traffic. Flush and down gre0 so
    # it cannot own the overlay subnet; delete any leftover mgre0 from a
    # prior run before recreating it. (Hence GRE_DEV=mgre0 instead of trying
    # to reconfigure the kernel's built-in gre0.)
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    # Linux derives ikey/okey from the multicast remote; peers share that group.
    step(
        name,
        f"ip tunnel add {GRE_DEV} mode gre "
        f"local {cfg['underlay']} remote {UNDERLAY_MCAST} ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} up")
    step(name, f"ip route replace {OVERLAY_MCAST}/32 dev {GRE_DEV}")
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

section("Overlay unicast across mGRE (before peer nrlsmf)")

for src, scfg in PEERS.items():
    for dst, dcfg in PEERS.items():
        if src == dst:
            continue
        wait_step(
            src,
            f"ping -c1 -W3 -I {GRE_DEV} {dcfg['overlay']}",
            match="1 received",
            desc=f"{src} ping overlay {dst} ({dcfg['overlay']})",
            timeout=20,
        )

section("Start peer nrlsmf classic flooding on mGRE (map + ujoin)")

for name, cfg in PEERS.items():
    # Overlay control/dataplane on peers (independent of r0 underlay instance):
    #   instance smf-{name}-mcast — unique control pipe (/tmp shared across munet ns)
    #   add overlay,cf,mgre0      — classic flooding on the GRE iface
    #   map mgre0,local,0.0.0.0   — GRE: mGRE/any-remote mapping for SMF lookup
    #   ujoin group,eth0          — GRE: join underlay mcast used for GRE encap/recv
    # forward/relay default to on, so they are omitted here.
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name}-mcast "
        f"add overlay,cf,{GRE_DEV} "
        f"map {GRE_DEV},{cfg['underlay']},0.0.0.0 "
        f"ujoin {UNDERLAY_MCAST},eth0 "
        f"&> nrlsmf-mgre-mcast.log &",
    )

for name in peer_names():
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

section("Overlay multicast through nrlsmf CF on mGRE")

for name in ("p2", "p3", "p4"):
    step(
        name,
        f"iperf -u -T 4 -i 1 -s -e -B {OVERLAY_MCAST}%{GRE_DEV} "
        f"> iperf-mgre-server.log 2>&1 &",
    )

step(
    "p1",
    f"iperf -u -T 4 -t 1000 -i 1 -b 8pps -l 1024 -e "
    f"-B {PEERS['p1']['overlay']}%{GRE_DEV} "
    f"-c {OVERLAY_MCAST} &> iperf-mgre-client.log &",
)

wait_step(
    "p1",
    "tail -n1 iperf-mgre-client.log",
    match="8 pps",
    desc="p1 sending overlay multicast at 8 pps",
    timeout=30,
)

for name in ("p2", "p3", "p4"):
    wait_step(
        name,
        "tail -n1 iperf-mgre-server.log",
        match="8 pps",
        desc=f"{name} receiving overlay multicast at 8 pps",
        timeout=45,
    )

section("Cleanup")

for name in peer_names():
    step(name, "pkill nrlsmf || true")
    step(name, "pkill iperf || true")
    wait_step(
        name,
        "pgrep -af nrlsmf || true",
        match="",
        desc=f"{name} nrlsmf stopped",
        timeout=15,
    )

step("r0", "pkill nrlsmf || true")
test_step(True, "mGRE underlay-mcast four-peer mutest completed")
