"""Example: "external" (metadata) GRE + nrlsmf CF among four routers.

Topology (shared with other tests in this directory):

         h0 -- r0 -- lan0 --\\
              r1 -- lan1 ---\\
                             u0   (underlay: routes ordinary IP between the LANs)
              r2 -- lan2 ---/
              r3 -- lan3 --/

What "external" (metadata) GRE means here
--------------------------------------------
Every other GRE/mGRE mode in this directory fixes its encapsulation
parameters (local address, and either a fixed remote or a wildcard
remote) directly *on the tunnel interface* -- that's what lets nrlsmf
read them straight back out via netlink with no configuration of its
own. An "external" (a.k.a. "collect metadata" or "lightweight") GRE
device has none of that: `ip link add ... type gre external` creates
an interface with no fixed local/remote/key at all. Instead, whatever
adds routes or flow rules for it supplies the encapsulation parameters
per destination, using Linux's lightweight tunnel ("lwtunnel") route
encap:

    ip route add <dst>/32 encap ip id <key> src <local> dst <remote> \\
        ttl <ttl> dev gre1

This is the mechanism SDN controllers and OVS/OVN typically use to
build many per-flow or per-peer tunnels dynamically out of a single
device, rather than the operator hand-configuring one interface per
peer. It's conceptually the multipoint-resolution equivalent of the
static NBMA table in mutest_mgre_static.py (mutest_mgre_static.py
resolves peers via `ip neigh` entries; this resolves them via
per-destination routes instead) -- just with the resolution table
expressed as routes rather than neighbor entries, and populated
externally rather than beingsomething nrlsmf or the kernel's GRE
driver can discover on its own.

Why this is the one case where nrlsmf's `map` command is required
---------------------------------------------------------------------
nrlsmf normally reads a GRE interface's local/remote endpoint addresses
straight from the kernel and needs no `map` command at all -- true for
every mode in this directory except this one. An external GRE device
reports no fixed local or remote address to read (they don't exist on
the interface itself), so nrlsmf has nothing to auto-discover. This
test deliberately starts nrlsmf once *without* `map` to show the
warning nrlsmf logs in that situation, then again *with* explicit
per-peer `map gre1,<local>,<peer>` entries. Overlay unicast still
uses the kernel lwtunnel routes (independent of `map`). Overlay
multicast inject onto this device has no single remote, so nrlsmf
transmits once per mapped unicast peer -- same send path as static
NBMA / NHRP. `map …,0.0.0.0` only records the wildcard; it is not a
send dest. `map …,dynamic` does not apply here (no `ip neigh` table).

What this example covers
-------------------------
* Underlay: unicast IP between all four routers, routed through u0.
* Overlay: one external GRE device (gre1) per router, sharing a single
  172.16.0.0/24 overlay subnet, with per-destination lwtunnel routes
  providing the encapsulation parameters to reach each other router --
  the "external" analogue of the NBMA table in mutest_mgre.py. gre1
  is used because the kernel's built-in fallback gre0 cannot be turned
  into a collect-md device (same reason mutest_mgre_mcast.py uses mgre0).
* nrlsmf classic flooding (`cf`) on each router's host LAN plus gre1:
    - First started *without* `map`, to confirm nrlsmf logs the
      documented "missing tunnel endpoint addressing... must map it"
      warning. Overlay unicast still works (kernel lwtunnel routes).
    - Then restarted with explicit `map gre1,<local>,<peer>` for every
      other router, so overlay-multicast inject has unicast remotes.
    - Iperf sourced on host h0, received at h1/h2/h3.

See mutest_gre_p2p.py (point-to-point), mutest_mgre_static.py (static
NBMA mGRE), mutest_mgre_nhrp.py (NHRP-resolved mGRE), and
mutest_mgre_mcast.py (multicast-underlay mGRE) for the other GRE
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
from kernel_compat import min_kernel_version

ROUTERS = {
    "r0": {"underlay": "10.0.0.2", "overlay": "172.16.0.1"},
    "r1": {"underlay": "10.0.1.2", "overlay": "172.16.0.2"},
    "r2": {"underlay": "10.0.2.2", "overlay": "172.16.0.3"},
    "r3": {"underlay": "10.0.3.2", "overlay": "172.16.0.4"},
}

# Dedicated name: kernel fallback gre0 is not a collect-md device and
# will steal inbound GRE (and the overlay subnet) if we try to reuse it.
GRE_DEV = "gre1"
GRE_KEY = "100"
MISSING_ENDPOINT_WARNING = "missing tunnel endpoint addressing"
OVERLAY_MCAST = "239.0.0.1"

if min_kernel_version((5, 0)):
    return "skip"


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

section("Create external (metadata) GRE devices with per-destination routes")

for name, cfg in ROUTERS.items():
    # Don't reuse kernel fallback gre0 as the external device: that
    # `ip link add` often fails (device exists), step() keeps going,
    # and overlay pings then hit a remote-any tunnel with no remotes.
    step(name, "ip addr flush dev gre0 2>/dev/null || true")
    step(name, "ip link set gre0 down 2>/dev/null || true")
    step(name, f"ip link del {GRE_DEV} 2>/dev/null || true")
    # No local/remote/key here at all -- that's what makes this
    # "external": the device carries none of its own encapsulation
    # parameters.
    step(name, f"ip link add name {GRE_DEV} type gre external")
    step(name, f"ip link set {GRE_DEV} multicast on")
    step(name, f"ip link set {GRE_DEV} up")
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    # Per-destination lwtunnel routes supply the encapsulation
    # parameters that would otherwise live on the interface. This is
    # the "external" analogue of the ip-neigh NBMA table in
    # mutest_mgre.py -- same job, different mechanism.
    for other, ocfg in ROUTERS.items():
        if other == name:
            continue
        step(
            name,
            f"ip route replace {ocfg['overlay']}/32 encap ip id {GRE_KEY} "
            f"src {cfg['underlay']} dst {ocfg['underlay']} ttl 64 "
            f"dev {GRE_DEV}",
        )
    wait_step(
        name,
        f"ip -br link show {GRE_DEV}",
        match="UP",
        desc=f"{name} {GRE_DEV} is UP",
    )
    wait_step(
        name,
        f"ip -d link show {GRE_DEV}",
        match="external",
        desc=f"{name} {GRE_DEV} is collect-md / external",
    )
    # Confirm one lwtunnel route actually installed (not a plain
    # on-link /24 leftover from a failed encap command).
    peer = next(n for n in ROUTERS if n != name)
    wait_step(
        name,
        f"ip route get {ROUTERS[peer]['overlay']}",
        match="encap",
        desc=f"{name} overlay route to {peer} uses lwtunnel encap",
    )

section("Overlay unicast across external GRE (kernel-level, before nrlsmf)")

# This works purely from the lwtunnel routes above -- nothing here
# depends on nrlsmf or its map state, which is the point being made in
# the next section.
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

section("Start nrlsmf WITHOUT map -- confirm the documented warning fires")

for name in ROUTERS:
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name}-ext-nomap "
        f"add overlay,cf,eth1,{GRE_DEV} "
        "&> nrlsmf-ext-nomap.log &",
    )
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}-ext-nomap"',
        match=f"smf-{name}-ext-nomap",
        desc=f"{name} nrlsmf running on {GRE_DEV} (no map)",
        timeout=20,
    )
    wait_step(
        name,
        "grep "
        f'"{MISSING_ENDPOINT_WARNING}" nrlsmf-ext-nomap.log',
        match=GRE_DEV,
        desc=f"{name} nrlsmf logs the missing-endpoint warning for {GRE_DEV}",
        timeout=20,
    )

section("Overlay unicast still works without map (kernel handles it, not nrlsmf)")

wait_step(
    "r0",
    f"ping -c1 -W3 -I {ROUTERS['r0']['overlay']} {ROUTERS['r1']['overlay']}",
    match="1 received",
    desc="r0 ping overlay r1 (still fine -- map affects nrlsmf bookkeeping only)",
    timeout=20,
)

section("Stop the un-mapped instances")

for name in ROUTERS:
    step(name, "pkill nrlsmf || true")
    wait_step(
        name,
        "pgrep -af nrlsmf || true",
        match="",
        desc=f"{name} nrlsmf stopped",
        timeout=15,
    )

section("Restart nrlsmf WITH per-peer map -- overlay mcast inject dests")

for name, cfg in ROUTERS.items():
    maps = " ".join(
        f"map {GRE_DEV},{cfg['underlay']},{ocfg['underlay']}"
        for other, ocfg in ROUTERS.items()
        if other != name
    )
    step(
        name,
        "nrlsmf debug 4 "
        f"instance smf-{name}-ext "
        f"add overlay,cf,eth1,{GRE_DEV} "
        f"{maps} "
        "&> nrlsmf-ext.log &",
    )
    wait_step(
        name,
        f'pgrep -af "nrlsmf.*instance smf-{name}-ext"',
        match=f"smf-{name}-ext",
        desc=f"{name} nrlsmf running on {GRE_DEV} (mapped)",
        timeout=20,
    )
    wait_step(
        name,
        'grep "regular group" nrlsmf-ext.log',
        match="overlay",
        desc=f"{name} nrlsmf log shows overlay group",
        timeout=20,
    )

section("[External] Overlay multicast: h0 -> SMF -> h1/h2/h3")

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

test_step(True, "External (metadata) GRE four-router mutest completed")
