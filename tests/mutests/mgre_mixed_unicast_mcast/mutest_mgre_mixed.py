"""Example: mGRE with mixed underlay multicast and unicast peers.

Topology:

         h0 -- r0 -- lan0 --\\     mcast-capable
         h1 -- r1 -- lan1 ---\\
         h2 -- r2 -- lan2 ---- u0   (rmerge eth0,eth1,eth2 only)
         h3 -- r3 -- lan3 ---/     unicast-only
         h4 -- r4 -- lan4 --/

All five routers share one wildcard-remote mGRE interface (gre1,
remote 0.0.0.0) on 172.16.0.0/24. Overlay unicast uses kernel
ip neigh. Overlay multicast inject is mixed:

* r0/r1/r2 join underlay group 239.1.1.1 (ujoin on eth0) and map that
  group as one inject dest, plus explicit unicast maps to r3 and r4.
* r3/r4 do not join the group; they map every other router as a
  unicast inject dest.

gre1 is layered so a packet received on the tunnel is not flooded
back out gre1 (unicast-only routers do not re-inject to the mcast
set). Traffic that arrives on eth1 is still injected onto gre1.
The overlay-multicast section captures GRE leaving r0 eth0 and
checks 8 overlay pps become 24 GRE (8 to 239.1.1.1, 8 to r3, 8 to r4).

u0 unicasts among all five LANs. Its nrlsmf rmerge is only on
eth0,eth1,eth2 so underlay multicast never reaches r3/r4. That nrlsmf
instance is a PIM stand-in only; it is not part of the overlay.
"""

from datetime import datetime
from datetime import timedelta

from munet.mutest.userapi import get_target
from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import sys

sys.path.insert(0, str(script_dir()))
sys.path.insert(0, str(script_dir().parent))
from mixed_hosts import RECV_HOSTS
from mixed_hosts import cleanup_iperf
from mixed_hosts import setup_host_lan
from mixed_hosts import start_host_mcast_client
from mixed_hosts import start_overlay_mcast_servers
from mixed_hosts import wait_overlay_mcast_receivers
from smf_cli import check_common_show
from smf_cli import check_show_neighbors
from smf_cli import check_show_tunnel

ROUTERS = {
    "r0": {"underlay": "10.0.0.2", "overlay": "172.16.0.1"},
    "r1": {"underlay": "10.0.1.2", "overlay": "172.16.0.2"},
    "r2": {"underlay": "10.0.2.2", "overlay": "172.16.0.3"},
    "r3": {"underlay": "10.0.3.2", "overlay": "172.16.0.4"},
    "r4": {"underlay": "10.0.4.2", "overlay": "172.16.0.5"},
}

MCAST_ROUTERS = ("r0", "r1", "r2")
UCAST_ONLY = ("r3", "r4")

UNDERLAY_MCAST = "239.1.1.1"
OVERLAY_MCAST = "239.0.0.1"
GRE_DEV = "gre1"
U0_MCAST_IFACES = "eth0,eth1,eth2"
GRE_DUMP = "tcpdump-r0-eth0-gre.log"
GRE_WINDOW_S = 1.0
GRE_PPS = 8
GRE_PPS_SLOP = 2


def gre_dest_counts(path, window_s=GRE_WINDOW_S):
    """Count GRE dests in the first ``window_s`` seconds of a tcpdump log."""
    samples = []
    for ln in path.read_text().splitlines():
        if "GREv0" not in ln:
            continue
        parts = ln.split()
        try:
            ts = datetime.strptime(parts[0], "%H:%M:%S.%f")
        except (ValueError, IndexError):
            continue
        dest = parts[4].rstrip(":")
        samples.append((ts, dest))
    counts = {}
    if not samples:
        return counts
    t1 = samples[0][0] + timedelta(seconds=window_s)
    for ts, dest in samples:
        if ts >= t1:
            break
        counts[dest] = counts.get(dest, 0) + 1
    return counts


def gre_pps_ok(n):
    return GRE_PPS - GRE_PPS_SLOP <= n <= GRE_PPS + GRE_PPS_SLOP


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
    ("eth4", "10.0.4.1"),
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

section("Underlay multicast relay on u0 (r0/r1/r2 only)")

# PIM stand-in for the mcast-capable LANs only. eth3/eth4 (r3/r4) are
# left out so those routers never see UNDERLAY_MCAST.
step(
    "u0",
    "nrlsmf debug 4 "
    "instance smf-u0-underlay "
    f"rmerge {U0_MCAST_IFACES} "
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

section("Create wildcard-remote mGRE tunnels (remote 0.0.0.0)")

for name, cfg in ROUTERS.items():
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    step(
        name,
        f"ip link add name {GRE_DEV} type gre "
        f"local {cfg['underlay']} remote 0.0.0.0 ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} multicast on")
    step(name, f"ip link set {GRE_DEV} up")
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

for name in MCAST_ROUTERS:
    step(name, f"ip route replace {UNDERLAY_MCAST}/32 dev eth0")

section("Overlay unicast across mGRE (before overlay nrlsmf)")

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

section("Start overlay nrlsmf (mixed inject dests)")

for name, cfg in ROUTERS.items():
    if name in MCAST_ROUTERS:
        maps = (
            f"map {GRE_DEV},{cfg['underlay']},{UNDERLAY_MCAST} "
            + " ".join(
                f"map {GRE_DEV},{cfg['underlay']},{ROUTERS[peer]['underlay']}"
                for peer in UCAST_ONLY
            )
        )
        ujoin = f"ujoin {UNDERLAY_MCAST},eth0 "
    else:
        maps = " ".join(
            f"map {GRE_DEV},{cfg['underlay']},{ocfg['underlay']}"
            for other, ocfg in ROUTERS.items()
            if other != name
        )
        ujoin = ""
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name}-mixed "
        f"add overlay,cf,eth1,{GRE_DEV} "
        f"layered {GRE_DEV} "
        f"{ujoin}"
        f"{maps} "
        "&> nrlsmf-mgre-mixed.log &",
    )
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}-mixed"',
        match=f"smf-{name}-mixed",
        desc=f"{name} nrlsmf running on {GRE_DEV}",
        timeout=20,
    )
    wait_step(
        name,
        'grep "regular group" nrlsmf-mgre-mixed.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("nrlsmf --cli show tunnel / neighbors (json, mixed inject dests)")

# r0/r1/r2 map underlay mcast + unicast-only remotes; r3/r4 map every other
# router as unicast. Overlay pings populate Neighbor IP from kernel neigh.
for name, cfg in ROUTERS.items():
    inst = f"smf-{name}-mixed"
    check_common_show(name, inst, group_name="overlay", ifaces=("eth1", GRE_DEV))
    if name in MCAST_ROUTERS:
        remotes = [UNDERLAY_MCAST] + [ROUTERS[p]["underlay"] for p in UCAST_ONLY]
    else:
        remotes = [ocfg["underlay"] for other, ocfg in ROUTERS.items() if other != name]
    check_show_tunnel(
        name, inst, GRE_DEV,
        local=cfg["underlay"],
        remotes=remotes,
        overlay_ip=cfg["overlay"],
        want_c=True,
    )
    check_show_neighbors(
        name, inst, GRE_DEV,
        remotes=remotes,
        neighbor_ips=[ocfg["overlay"] for other, ocfg in ROUTERS.items() if other != name],
        min_count=len(remotes),
        want_c=True,
    )

section("[Mixed] Overlay multicast: h0 -> SMF -> h1/h2/h3/h4")

RECEIVERS = RECV_HOSTS
start_overlay_mcast_servers(step, RECEIVERS, OVERLAY_MCAST)
start_host_mcast_client(step, wait_step, OVERLAY_MCAST)

# 8 overlay pps x 3 mapped remotes = 24 GRE/s on r0 eth0. Capture a
# few seconds into r0's rundir, then count dests in a 1s timestamp
# window (not the first N packets).
step(
    "r0",
    "timeout 3 tcpdump -lnni eth0 "
    f"'proto gre and src host {ROUTERS['r0']['underlay']}' "
    f"&> {GRE_DUMP} || true",
)
counts = gre_dest_counts(get_target("r0").rundir / GRE_DUMP)
n_mcast = counts.get(UNDERLAY_MCAST, 0)
n_r3 = counts.get(ROUTERS["r3"]["underlay"], 0)
n_r4 = counts.get(ROUTERS["r4"]["underlay"], 0)
n_r1 = counts.get(ROUTERS["r1"]["underlay"], 0)
n_r2 = counts.get(ROUTERS["r2"]["underlay"], 0)
test_step(
    gre_pps_ok(n_mcast)
    and gre_pps_ok(n_r3)
    and gre_pps_ok(n_r4)
    and n_r1 == 0
    and n_r2 == 0,
    f"r0 eth0 1s GRE: mcast={n_mcast} r3={n_r3} r4={n_r4} "
    f"r1={n_r1} r2={n_r2} (expect {GRE_PPS}±{GRE_PPS_SLOP} each)",
    "r0",
)

wait_overlay_mcast_receivers(wait_step, RECEIVERS)

section("Cleanup")

cleanup_iperf(step, RECEIVERS)
step("r0", "pkill tcpdump || true")
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
test_step(True, "mGRE mixed unicast/mcast five-router mutest completed")
