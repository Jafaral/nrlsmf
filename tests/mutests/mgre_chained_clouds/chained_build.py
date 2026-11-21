"""Shared build steps for the chained-clouds GRE/mGRE mutests.

build_network() builds the five underlay segments, every GRE/mGRE
overlay, and u2's underlay multicast stand-in. start_nrlsmf() starts
nrlsmf (classic flooding group "overlay") on every overlay router.
See mutest_chained_clouds.py for the topology and why each segment is
configured the way it is.
"""

from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import wait_step

from chained_hosts import setup_host_lan


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


OVERLAY_ROUTERS = ("A", "A2", "B", "B2", "C", "C2", "D", "E", "E2", "F")


def build_network():
    """Underlay segments, all five GRE/mGRE overlays, overlay reachability."""
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


def start_nrlsmf(env="", tag=""):
    """Start nrlsmf on every overlay router.

    env is prefixed to each nrlsmf command (for example test hooks);
    tag is appended to each log name (nrlsmf-<name><tag>.log).
    """
    if env and not env.endswith(" "):
        env += " "
    section(f"Start nrlsmf on overlay routers{tag}")

    # cloud0: A learns inject dests from neigh; A2/B pin them.
    # cloud1: nhrpd programs neigh; B/C (spokes) map dynamic + layered;
    #         B2 (hub) map dynamic, not layered (overlay replicator).
    # cloud2: ujoin on the underlay iface.
    # cloud3: one configured remote, no map.
    # cloud4: explicit unicast maps (no neigh to learn).
    step(
        "A",
        f"{env}nrlsmf debug 4 "
        "instance smf-A "
        f"add overlay,cf,eth1,{GRE_C0} "
        f"layered {GRE_C0} "
        f"map {GRE_C0},{C0_PEERS['A']['underlay']},dynamic "
        f"&> nrlsmf-A{tag}.log &",
    )
    step(
        "A2",
        f"{env}nrlsmf debug 4 "
        "instance smf-A2 "
        f"add overlay,cf,eth1,{GRE_C0} "
        f"layered {GRE_C0} "
        f"map {GRE_C0},{C0_PEERS['A2']['underlay']},{C0_PEERS['A']['underlay']} "
        f"map {GRE_C0},{C0_PEERS['A2']['underlay']},{C0_PEERS['B']['underlay']} "
        f"&> nrlsmf-A2{tag}.log &",
    )
    step(
        "B",
        f"{env}nrlsmf debug 4 "
        "instance smf-B "
        f"add overlay,cf,{GRE_C0},{GRE_C1} "
        f"layered {GRE_C0},{GRE_C1} "
        f"map {GRE_C0},{C0_PEERS['B']['underlay']},{C0_PEERS['A']['underlay']} "
        f"map {GRE_C0},{C0_PEERS['B']['underlay']},{C0_PEERS['A2']['underlay']} "
        f"map {GRE_C1},{C1_PEERS['B']['underlay']},dynamic "
        f"&> nrlsmf-B{tag}.log &",
    )
    step(
        "B2",
        f"{env}nrlsmf debug 4 "
        "instance smf-B2 "
        f"add overlay,cf,eth1,{GRE_C1} "
        f"map {GRE_C1},{C1_PEERS['B2']['underlay']},dynamic "
        f"&> nrlsmf-B2{tag}.log &",
    )
    step(
        "C",
        f"{env}nrlsmf debug 4 "
        "instance smf-C "
        f"add overlay,cf,{GRE_C1},{GRE_C2} "
        f"layered {GRE_C1},{GRE_C2} "
        f"map {GRE_C1},{C1_PEERS['C']['underlay']},dynamic "
        f"ujoin {UNDERLAY_MCAST_C2},{C2_PEERS['C']['uj_iface']} "
        f"&> nrlsmf-C{tag}.log &",
    )
    step(
        "C2",
        f"{env}nrlsmf debug 4 "
        "instance smf-C2 "
        f"add overlay,cf,eth1,{GRE_C2} "
        f"layered {GRE_C2} "
        f"ujoin {UNDERLAY_MCAST_C2},{C2_PEERS['C2']['uj_iface']} "
        f"&> nrlsmf-C2{tag}.log &",
    )
    step(
        "D",
        f"{env}nrlsmf debug 4 "
        "instance smf-D "
        f"add overlay,cf,{GRE_C2},{GRE_P2P} "
        f"layered {GRE_C2},{GRE_P2P} "
        f"ujoin {UNDERLAY_MCAST_C2},{C2_PEERS['D']['uj_iface']} "
        f"&> nrlsmf-D{tag}.log &",
    )
    step(
        "E",
        f"{env}nrlsmf debug 4 "
        "instance smf-E "
        f"add overlay,cf,{GRE_P2P},{GRE_C4} "
        f"layered {GRE_P2P},{GRE_C4} "
        f"map {GRE_C4},{C4_PEERS['E']['underlay']},{C4_PEERS['E2']['underlay']} "
        f"map {GRE_C4},{C4_PEERS['E']['underlay']},{C4_PEERS['F']['underlay']} "
        f"&> nrlsmf-E{tag}.log &",
    )
    step(
        "E2",
        f"{env}nrlsmf debug 4 "
        "instance smf-E2 "
        f"add overlay,cf,{GRE_C4} "
        f"layered {GRE_C4} "
        f"map {GRE_C4},{C4_PEERS['E2']['underlay']},{C4_PEERS['E']['underlay']} "
        f"map {GRE_C4},{C4_PEERS['E2']['underlay']},{C4_PEERS['F']['underlay']} "
        f"&> nrlsmf-E2{tag}.log &",
    )
    step(
        "F",
        f"{env}nrlsmf debug 4 "
        "instance smf-F "
        f"add overlay,cf,eth1,{GRE_C4} "
        f"layered {GRE_C4} "
        f"map {GRE_C4},{C4_PEERS['F']['underlay']},{C4_PEERS['E']['underlay']} "
        f"map {GRE_C4},{C4_PEERS['F']['underlay']},{C4_PEERS['E2']['underlay']} "
        f"&> nrlsmf-F{tag}.log &",
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
            f'grep "regular group" nrlsmf-{name}{tag}.log',
            match="overlay",
            desc=f"{name} nrlsmf log shows overlay group",
            timeout=20,
        )
