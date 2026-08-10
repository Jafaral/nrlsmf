"""Example: multipoint GRE (NBMA) + nrlsmf CF among four peers.

Topology (shared with other tests in this directory):

  p1 -- lan1 --\
  p2 -- lan2 ---\
                 r0   (hub; static/connected underlay routes)
  p3 -- lan3 ---/
  p4 -- lan4 --/

What this example covers
------------------------
* Underlay: unicast IP between peers through hub router r0.
* Overlay: multipoint GRE (remote any) with static NBMA neighbors
  (overlay IP -> underlay IP), similar to a static DMVPN-style mesh.
* nrlsmf on each peer running classic flooding on gre0, with GRE helpers:
  - map gre0,<local>,0.0.0.0  (mGRE / any-remote tunnel mapping)
  - ujoin <group>,eth0        (underlay group join API)

See mutest_mgre_mcast.py for the underlay-multicast GRE remote example.
"""

from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

# Underlay LAN addressing (matches */etc.frr/frr.conf)
PEERS = {
    "p1": {"underlay": "10.0.1.2", "overlay": "172.16.0.1"},
    "p2": {"underlay": "10.0.2.2", "overlay": "172.16.0.2"},
    "p3": {"underlay": "10.0.3.2", "overlay": "172.16.0.3"},
    "p4": {"underlay": "10.0.4.2", "overlay": "172.16.0.4"},
}

# Used with ujoin (GRE underlay join API).
UNDERLAY_MCAST = "239.1.1.1"
OVERLAY_MCAST = "239.0.0.1"
GRE_DEV = "gre0"


def peer_names():
    return list(PEERS.keys())


section("Disable offloads and wait for underlay addresses")

step("r0", "sysctl -w net.ipv4.ip_forward=1")

for name, cfg in PEERS.items():
    step(name, "ethtool -K eth0 rx off tx off || true")
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

section("Create multipoint GRE tunnels with NBMA neighbors")

for name, cfg in PEERS.items():
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    step(
        name,
        f"ip link add name {GRE_DEV} type gre "
        f"local {cfg['underlay']} remote 0.0.0.0 ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} up")
    # Map each remote overlay address to that peer's underlay endpoint.
    for other, ocfg in PEERS.items():
        if other == name:
            continue
        step(
            name,
            f"ip neigh replace {ocfg['overlay']} lladdr {ocfg['underlay']} "
            f"nud permanent dev {GRE_DEV}",
        )
    step(name, f"ip route replace {OVERLAY_MCAST}/32 dev {GRE_DEV}")
    wait_step(
        name,
        f"ip -br link show {GRE_DEV}",
        match="UP",
        desc=f"{name} {GRE_DEV} is UP",
    )

section("Overlay unicast across mGRE (before nrlsmf)")

for src, scfg in PEERS.items():
    for dst, dcfg in PEERS.items():
        if src == dst:
            continue
        wait_step(
            src,
            f"ping -c1 -W3 -I {scfg['overlay']} {dcfg['overlay']}",
            match="1 received",
            desc=f"{src} ping overlay {dst} ({dcfg['overlay']})",
            timeout=20,
        )

section("Start nrlsmf classic flooding on mGRE (map + ujoin)")

for name, cfg in PEERS.items():
    # nrlsmf startup for GRE/mGRE:
    #   instance smf-{name}  — unique control pipe (/tmp is shared across munet ns)
    #   add overlay,cf,gre0  — classic flooding on the GRE iface
    #   map gre0,local,0.0.0.0 — GRE: tunnel endpoints; 0.0.0.0 = mGRE/any-remote
    #   ujoin group,eth0     — GRE: underlay mcast join for mGRE reception
    # forward/relay default to on, so they are omitted here.
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name} "
        f"add overlay,cf,{GRE_DEV} "
        f"map {GRE_DEV},{cfg['underlay']},0.0.0.0 "
        f"ujoin {UNDERLAY_MCAST},eth0 "
        f"&> nrlsmf-mgre.log &",
    )

for name in peer_names():
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

section("Cleanup")

for name in peer_names():
    step(name, "pkill nrlsmf || true")
    wait_step(
        name,
        "pgrep -af nrlsmf || true",
        match="",
        desc=f"{name} nrlsmf stopped",
        timeout=15,
    )

test_step(True, "mGRE NBMA four-peer mutest completed")
