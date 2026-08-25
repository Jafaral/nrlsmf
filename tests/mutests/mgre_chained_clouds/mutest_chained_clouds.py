"""Example: one chained network exercising all five GRE/mGRE modes.

Topology:

    ha   ha2         hb2        hc2                        hf
    |     |          |          |                          |
    |     A2         B2         C2                   E2    |
    |     |          |          |                    |     |
    A -- u0 -- B -- u1 -- C -- u2 -- D -- u3 -- E -- u4 -- F

  cloud0 (u0, 3 peers): A, A2, B           -- static NBMA mGRE
  cloud1 (u1, 3 peers): B, B2 (hub/NHS), C  -- NHRP-resolved mGRE
  cloud2 (u2, 3 peers): C, C2, D            -- multicast-underlay mGRE
  cloud3 (u3, 2 peers): D, E                -- point-to-point GRE
  cloud4 (u4, 3 peers): E, E2, F            -- external (metadata) GRE

  ha is the iperf source (off A). ha2 / hb2 / hc2 / hf are receivers
  off A2 / B2 / C2 / F -- one host on each cloud, last hop is nrlsmf
  CF onto that host LAN.

Why this test exists
---------------------
Every other test in tests/mutests/mgre_four_peers/ demonstrates one
GRE/mGRE mode in isolation. Real deployments chain several of these
together -- e.g. a MANET island using static NBMA mGRE, gatewayed
through a DMVPN-style WAN using NHRP, onward through a satellite hop
using multicast-underlay mGRE, and finally into an SDN-managed segment
using external/metadata GRE. This test builds exactly that kind of
chain, five segments end to end, and proves a single multicast flow
can cross all five.

u0 through u4 are five *separate*, disconnected underlay routers --
there is no IP route anywhere in this topology from one segment's
addressing to the next. B, C, D, and E each sit on two adjacent
clouds, but their frr.conf routes only reach into those clouds. The
only thing connecting cloud0's traffic all the way to cloud4 is nrlsmf
running in classical-flooding (`cf`) mode across both of each SMF
router's overlay interfaces -- never IP routing. u2 uses `rmerge` only
as a stand-in for underlay multicast routing (PIM), not as an overlay
SMF router.

What this example covers
-------------------------
* Building all five GRE/mGRE modes in a single network.
* Overlay-multicast inject dests mixed across nodes:
    - cloud0: A `map …,dynamic`; A2 and B explicit unicast `map`s
    - cloud1: B and C `map …,dynamic` (spoke→hub); B2 `map …,dynamic`
      and is not layered (hub replicates to the other spoke)
    - cloud2: `ujoin` on the underlay iface (single multicast remote)
    - cloud3: no `map` (one configured remote)
    - cloud4: explicit unicast `map`s (`dynamic` does not apply)
* Application multicast sourced on ha, received on ha2/hb2/hc2/hf.

See tests/mutests/mgre_four_peers/ for each of these five modes
documented and tested individually.
"""

from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import sys

sys.path.insert(0, str(script_dir()))
sys.path.insert(0, str(script_dir().parent))
from chained_hosts import RECV_HOSTS
from chained_hosts import cleanup_iperf
from chained_hosts import setup_host_lan
from chained_hosts import start_host_mcast_client
from chained_hosts import start_overlay_mcast_servers
from chained_hosts import wait_overlay_mcast_receivers
from kernel_compat import min_kernel_version

# ---------------------------------------------------------------------
# cloud0 (u0): static NBMA mGRE -- A, A2, B
# ---------------------------------------------------------------------
GRE_C0 = "gre_c0"
C0_PEERS = {
    "A": {"underlay": "10.0.0.2", "overlay": "172.16.0.1"},
    "A2": {"underlay": "10.0.1.2", "overlay": "172.16.0.2"},
    "B": {"underlay": "10.0.2.2", "overlay": "172.16.0.3"},
}

# ---------------------------------------------------------------------
# cloud1 (u1): NHRP-resolved mGRE -- B, B2 (hub/NHS), C
# ---------------------------------------------------------------------
GRE_C1 = "gre_c1"
GRE_KEY_C1 = "42"
C1_HUB = "B2"
C1_PEERS = {
    "B": {"underlay": "10.1.0.2", "overlay": "172.17.0.2"},
    "B2": {"underlay": "10.1.1.2", "overlay": "172.17.0.1"},
    "C": {"underlay": "10.1.2.2", "overlay": "172.17.0.3"},
}

# ---------------------------------------------------------------------
# cloud2 (u2): multicast-underlay mGRE -- C, C2, D
# u2 itself runs `rmerge` across its three LANs as a PIM stand-in.
# ---------------------------------------------------------------------
GRE_C2 = "gre_c2"
UNDERLAY_MCAST_C2 = "239.1.1.1"
C2_PEERS = {
    "C": {"underlay": "10.2.0.2", "overlay": "172.18.0.1", "uj_iface": "eth1"},
    "C2": {"underlay": "10.2.1.2", "overlay": "172.18.0.2", "uj_iface": "eth0"},
    "D": {"underlay": "10.2.2.2", "overlay": "172.18.0.3", "uj_iface": "eth0"},
}
U2_IFACES = "eth0,eth1,eth2"

# ---------------------------------------------------------------------
# cloud3 (u3): point-to-point GRE -- D <-> E
# ---------------------------------------------------------------------
GRE_P2P = "gre_p2p"
P2P_PEERS = {
    "D": {"underlay": "10.3.0.2", "overlay": "172.19.0.1"},
    "E": {"underlay": "10.3.1.2", "overlay": "172.19.0.2"},
}

# ---------------------------------------------------------------------
# cloud4 (u4): external (metadata) GRE -- E, E2, F
# ---------------------------------------------------------------------
GRE_C4 = "gre_c4"
GRE_KEY_C4 = "300"
C4_PEERS = {
    "E": {"underlay": "10.4.0.2", "overlay": "172.20.0.1"},
    "E2": {"underlay": "10.4.1.2", "overlay": "172.20.0.2"},
    "F": {"underlay": "10.4.2.2", "overlay": "172.20.0.3"},
}

OVERLAY_MCAST = "239.0.0.1"

UNDERLAY_ROUTERS = {
    "u0": {"eth0": "10.0.0.1", "eth1": "10.0.1.1", "eth2": "10.0.2.1"},
    "u1": {"eth0": "10.1.0.1", "eth1": "10.1.1.1", "eth2": "10.1.2.1"},
    "u2": {"eth0": "10.2.0.1", "eth1": "10.2.1.1", "eth2": "10.2.2.1"},
    "u3": {"eth0": "10.3.0.1", "eth1": "10.3.1.1"},
    "u4": {"eth0": "10.4.0.1", "eth1": "10.4.1.1", "eth2": "10.4.2.1"},
}
LEAF_NODES = {
    "C2": {"eth0": "10.2.1.2"},
    "E2": {"eth0": "10.4.1.2"},
    "F": {"eth0": "10.4.2.2"},
}
SMF_ROUTERS = {
    "A": {"eth0": "10.0.0.2"},
    "A2": {"eth0": "10.0.1.2"},
    "B": {"eth0": "10.0.2.2", "eth1": "10.1.0.2"},
    "B2": {"eth0": "10.1.1.2"},
    "C": {"eth0": "10.1.2.2", "eth1": "10.2.0.2"},
    "D": {"eth0": "10.2.2.2", "eth1": "10.3.0.2"},
    "E": {"eth0": "10.3.1.2", "eth1": "10.4.0.2"},
}

ALL_NODE_ADDRS = {**UNDERLAY_ROUTERS, **LEAF_NODES, **SMF_ROUTERS}

if min_kernel_version((5, 0)):
    return "skip"


section("Disable offloads and wait for underlay addresses")

for name, ifaces in ALL_NODE_ADDRS.items():
    for ifname, addr in ifaces.items():
        step(name, f"ethtool -K {ifname} rx off tx off || true")
        wait_step(
            name,
            f"ip -br addr show dev {ifname}",
            match=addr,
            desc=f"{name} {ifname} address {addr}",
            timeout=30,
        )

for name in UNDERLAY_ROUTERS:
    step(name, "sysctl -w net.ipv4.ip_forward=1")

setup_host_lan(step, wait_step)

section("Underlay reachability within each segment (never across segments)")

wait_step("A", "ping -c1 -W2 10.0.2.2", match="1 received", desc="A reaches B within cloud0", timeout=20)
wait_step("B2", "ping -c1 -W2 10.1.0.2", match="1 received", desc="B2 reaches B within cloud1", timeout=20)
wait_step("C2", "ping -c1 -W2 10.2.2.2", match="1 received", desc="C2 reaches D within cloud2", timeout=20)
wait_step("D", "ping -c1 -W2 10.3.1.2", match="1 received", desc="D reaches E within cloud3", timeout=20)
wait_step("E2", "ping -c1 -W2 10.4.2.2", match="1 received", desc="E2 reaches F within cloud4", timeout=20)


section("Build cloud0: static NBMA mGRE (A, A2, B)")

for name, cfg in C0_PEERS.items():
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_C0} 2>/dev/null || true")
    step(
        name,
        f"ip link add name {GRE_C0} type gre "
        f"local {cfg['underlay']} remote 0.0.0.0 ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_C0}")
    step(name, f"ip link set {GRE_C0} multicast on")
    step(name, f"ip link set {GRE_C0} up")
    for other, ocfg in C0_PEERS.items():
        if other == name:
            continue
        step(
            name,
            f"ip neigh replace {ocfg['overlay']} lladdr {ocfg['underlay']} "
            f"nud permanent dev {GRE_C0}",
        )
    wait_step(name, f"ip -br link show {GRE_C0}", match="UP", desc=f"{name} {GRE_C0} is UP")

section("Build cloud1: NHRP-resolved mGRE (B, B2=hub/NHS, C)")

for name, cfg in C1_PEERS.items():
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_C1} 2>/dev/null || true")
    step(
        name,
        f"ip tunnel add {GRE_C1} mode gre "
        f"local {cfg['underlay']} key {GRE_KEY_C1} ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/32 dev {GRE_C1}")
    step(name, f"ip link set {GRE_C1} multicast on")
    step(name, f"ip link set {GRE_C1} up")
    wait_step(name, f"ip -br link show {GRE_C1}", match="UP", desc=f"{name} {GRE_C1} is UP")

wait_step(C1_HUB, "pgrep -af nhrpd", match="nhrpd", desc=f"{C1_HUB} nhrpd is running", timeout=20)
step(
    C1_HUB,
    f"vtysh -c 'configure terminal' "
    f"-c 'interface {GRE_C1}' "
    f"-c 'ip nhrp network-id 1' "
    f"-c 'ip nhrp registration no-unique'",
)

for name in ("B", "C"):
    wait_step(name, "pgrep -af nhrpd", match="nhrpd", desc=f"{name} nhrpd is running", timeout=20)
    step(
        name,
        f"vtysh -c 'configure terminal' "
        f"-c 'interface {GRE_C1}' "
        f"-c 'ip nhrp network-id 1' "
        f"-c 'ip nhrp nhs {C1_PEERS[C1_HUB]['overlay']} "
        f"nbma {C1_PEERS[C1_HUB]['underlay']}' "
        f"-c 'ip nhrp registration no-unique'",
    )

for name in ("B", "C"):
    wait_step(
        C1_HUB,
        "vtysh -c 'show ip nhrp cache'",
        match=C1_PEERS[name]["overlay"],
        desc=f"{C1_HUB} NHRP cache shows {name} registered",
        timeout=60,
    )

section("Build cloud2: multicast-underlay mGRE (C, C2, D) + u2 as PIM stand-in")

step("u2", f"nrlsmf debug 4 instance smf-u2-underlay rmerge {U2_IFACES} &> nrlsmf-u2-underlay.log &")
wait_step(
    "u2",
    'pgrep -af "nrlsmf.*instance smf-u2-underlay"',
    match="smf-u2-underlay",
    desc="u2 underlay nrlsmf (PIM stand-in) running",
    timeout=20,
)
wait_step(
    "u2",
    'grep "regular group" nrlsmf-u2-underlay.log',
    match="merge",
    desc="u2 nrlsmf log shows merge group",
    timeout=20,
)

for name, cfg in C2_PEERS.items():
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_C2} 2>/dev/null || true")
    step(
        name,
        f"ip tunnel add {GRE_C2} mode gre "
        f"local {cfg['underlay']} remote {UNDERLAY_MCAST_C2} ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_C2}")
    step(name, f"ip link set {GRE_C2} multicast on")
    step(name, f"ip link set {GRE_C2} up")
    wait_step(name, f"ip -br link show {GRE_C2}", match="UP", desc=f"{name} {GRE_C2} is UP")

section("Build cloud3: point-to-point GRE (D, E)")

for local, remote in (("D", "E"), ("E", "D")):
    step(local, "ip addr flush dev gre0 2>/dev/null || true")
    step(local, "ip link set gre0 down 2>/dev/null || true")
    step(local, f"ip link del {GRE_P2P} 2>/dev/null || true")
    step(
        local,
        f"ip link add name {GRE_P2P} type gre "
        f"local {P2P_PEERS[local]['underlay']} "
        f"remote {P2P_PEERS[remote]['underlay']} ttl 64",
    )
    step(local, f"ip addr add {P2P_PEERS[local]['overlay']}/30 dev {GRE_P2P}")
    step(local, f"ip link set {GRE_P2P} multicast on")
    step(local, f"ip link set {GRE_P2P} up")
    wait_step(local, f"ip -br link show {GRE_P2P}", match="UP", desc=f"{local} {GRE_P2P} is UP")

section("Build cloud4: external (metadata) GRE (E, E2, F)")

for name, cfg in C4_PEERS.items():
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_C4} 2>/dev/null || true")
    step(name, f"ip link add name {GRE_C4} type gre external")
    step(name, f"ip link set {GRE_C4} multicast on")
    step(name, f"ip link set {GRE_C4} up")
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_C4}")
    for other, ocfg in C4_PEERS.items():
        if other == name:
            continue
        step(
            name,
            f"ip route replace {ocfg['overlay']}/32 encap ip id {GRE_KEY_C4} "
            f"src {cfg['underlay']} dst {ocfg['underlay']} ttl 64 dev {GRE_C4}",
        )
    wait_step(name, f"ip -br link show {GRE_C4}", match="UP", desc=f"{name} {GRE_C4} is UP")

section("Overlay unicast reachability within each segment (before nrlsmf)")

wait_step("A", f"ping -c1 -W3 -I {C0_PEERS['A']['overlay']} {C0_PEERS['B']['overlay']}",
          match="1 received", desc="A reaches B over cloud0 overlay", timeout=20)
wait_step("B2", f"ping -c1 -W3 -I {C1_PEERS['B2']['overlay']} {C1_PEERS['C']['overlay']}",
          match="1 received", desc="B2 reaches C over cloud1 overlay", timeout=20)
wait_step("C2", f"ping -c1 -W3 -I {GRE_C2} {C2_PEERS['D']['overlay']}",
          match="1 received", desc="C2 reaches D over cloud2 overlay", timeout=20)
wait_step("D", f"ping -c1 -W3 {P2P_PEERS['E']['overlay']}",
          match="1 received", desc="D reaches E over cloud3 overlay", timeout=20)
wait_step("E2", f"ping -c1 -W3 -I {C4_PEERS['E2']['overlay']} {C4_PEERS['F']['overlay']}",
          match="1 received", desc="E2 reaches F over cloud4 overlay", timeout=20)

section("Start nrlsmf on overlay routers")

# cloud0: A learns inject dests from neigh; A2/B pin them.
# cloud1: nhrpd programs neigh; B/C (spokes) map dynamic + layered;
#         B2 (hub) map dynamic, not layered (overlay replicator).
# cloud2: ujoin on the underlay iface.
# cloud3: one configured remote, no map.
# cloud4: explicit unicast maps (no neigh to learn).
step(
    "A",
    "nrlsmf debug 4 "
    "instance smf-A "
    f"add overlay,cf,eth1,{GRE_C0} "
    f"layered {GRE_C0} "
    f"map {GRE_C0},{C0_PEERS['A']['underlay']},dynamic "
    "&> nrlsmf-A.log &",
)
step(
    "A2",
    "nrlsmf debug 4 "
    "instance smf-A2 "
    f"add overlay,cf,eth1,{GRE_C0} "
    f"layered {GRE_C0} "
    f"map {GRE_C0},{C0_PEERS['A2']['underlay']},{C0_PEERS['A']['underlay']} "
    f"map {GRE_C0},{C0_PEERS['A2']['underlay']},{C0_PEERS['B']['underlay']} "
    "&> nrlsmf-A2.log &",
)
step(
    "B",
    "nrlsmf debug 4 "
    "instance smf-B "
    f"add overlay,cf,{GRE_C0},{GRE_C1} "
    f"layered {GRE_C0},{GRE_C1} "
    f"map {GRE_C0},{C0_PEERS['B']['underlay']},{C0_PEERS['A']['underlay']} "
    f"map {GRE_C0},{C0_PEERS['B']['underlay']},{C0_PEERS['A2']['underlay']} "
    f"map {GRE_C1},{C1_PEERS['B']['underlay']},dynamic "
    "&> nrlsmf-B.log &",
)
step(
    "B2",
    "nrlsmf debug 4 "
    "instance smf-B2 "
    f"add overlay,cf,eth1,{GRE_C1} "
    f"map {GRE_C1},{C1_PEERS['B2']['underlay']},dynamic "
    "&> nrlsmf-B2.log &",
)
step(
    "C",
    "nrlsmf debug 4 "
    "instance smf-C "
    f"add overlay,cf,{GRE_C1},{GRE_C2} "
    f"layered {GRE_C1},{GRE_C2} "
    f"map {GRE_C1},{C1_PEERS['C']['underlay']},dynamic "
    f"ujoin {UNDERLAY_MCAST_C2},{C2_PEERS['C']['uj_iface']} "
    "&> nrlsmf-C.log &",
)
step(
    "C2",
    "nrlsmf debug 4 "
    "instance smf-C2 "
    f"add overlay,cf,eth1,{GRE_C2} "
    f"layered {GRE_C2} "
    f"ujoin {UNDERLAY_MCAST_C2},{C2_PEERS['C2']['uj_iface']} "
    "&> nrlsmf-C2.log &",
)
step(
    "D",
    "nrlsmf debug 4 "
    "instance smf-D "
    f"add overlay,cf,{GRE_C2},{GRE_P2P} "
    f"layered {GRE_C2},{GRE_P2P} "
    f"ujoin {UNDERLAY_MCAST_C2},{C2_PEERS['D']['uj_iface']} "
    "&> nrlsmf-D.log &",
)
step(
    "E",
    "nrlsmf debug 4 "
    "instance smf-E "
    f"add overlay,cf,{GRE_P2P},{GRE_C4} "
    f"layered {GRE_P2P},{GRE_C4} "
    f"map {GRE_C4},{C4_PEERS['E']['underlay']},{C4_PEERS['E2']['underlay']} "
    f"map {GRE_C4},{C4_PEERS['E']['underlay']},{C4_PEERS['F']['underlay']} "
    "&> nrlsmf-E.log &",
)
step(
    "E2",
    "nrlsmf debug 4 "
    "instance smf-E2 "
    f"add overlay,cf,{GRE_C4} "
    f"layered {GRE_C4} "
    f"map {GRE_C4},{C4_PEERS['E2']['underlay']},{C4_PEERS['E']['underlay']} "
    f"map {GRE_C4},{C4_PEERS['E2']['underlay']},{C4_PEERS['F']['underlay']} "
    "&> nrlsmf-E2.log &",
)
step(
    "F",
    "nrlsmf debug 4 "
    "instance smf-F "
    f"add overlay,cf,eth1,{GRE_C4} "
    f"layered {GRE_C4} "
    f"map {GRE_C4},{C4_PEERS['F']['underlay']},{C4_PEERS['E']['underlay']} "
    f"map {GRE_C4},{C4_PEERS['F']['underlay']},{C4_PEERS['E2']['underlay']} "
    "&> nrlsmf-F.log &",
)

for name in ("A", "A2", "B", "B2", "C", "C2", "D", "E", "E2", "F"):
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}"',
        match=f"smf-{name}",
        desc=f"{name} nrlsmf running",
        timeout=20,
    )
    wait_step(
        name,
        f'grep "regular group" nrlsmf-{name}.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("Overlay multicast: ha -> ha2 / hb2 / hc2 / hf")

start_overlay_mcast_servers(step, RECV_HOSTS, OVERLAY_MCAST)
start_host_mcast_client(step, wait_step, OVERLAY_MCAST)
wait_overlay_mcast_receivers(wait_step, RECV_HOSTS)

section("Cleanup")

cleanup_iperf(step, RECV_HOSTS)

for name in (
    "A", "A2", "B", "B2", "C", "C2", "D", "E", "E2", "F", "u2",
):
    step(name, "pkill nrlsmf || true")
    wait_step(
        name,
        'pgrep -af "nrlsmf" || true',
        match="",
        desc=f"{name} nrlsmf stopped",
        timeout=15,
    )

test_step(True, "chained five-mode network mutest completed")
