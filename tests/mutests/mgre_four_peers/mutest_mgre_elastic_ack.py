"""Elastic multicast acknowledgements over multipoint GRE (static NBMA).

Topology (shared with other tests in this directory):

        h0 -- r0 -- lan0 --\\
        h1 -- r1 -- lan1 ---\\
                             u0   (underlay: routes ordinary IP between the LANs)
        h2 -- r2 -- lan2 ---/
        h3 -- r3 -- lan3 --/

What this test is about
------------------------
With elastic multicast, a router with local members acknowledges a flow
(EM_ACK) to its previous hop, the relay it receives that flow from. The
relay then switches its outbound interface for the flow from LIMIT to
FORWARD. On a multipoint GRE device every neighbor shares one interface,
so the EM_ACK must be encapsulated to the right neighbor: the outer GRE
destination must be the relay's underlay address.

The previous hop on such a device is the outer GRE source of the packets
received from it. If it is identified wrongly (for example as the inner
IP source, which is the flow source), the EM_ACK is encapsulated toward
the flow source instead. The relay never gets it, so it stays in LIMIT,
while the receiver still shows the tunnel as its selected ingress.

Flows
-----
* G1 239.0.0.1 from h0 (relay r0): members h1 only.
* G2 239.0.0.2 from h2 (relay r2): members h0, h1, h3.

Two relays and several acknowledging routers per flow make sure each
EM_ACK reaches the relay of its own flow, not just "some" neighbor.

Phases
------
1. Normal operation.
2. NRLSMF_TEST_CAPTURE_INNER_SRC=1: the packet capture reports the inner
   IP source as a packet's source on the GRE device, as some kernels do.
   nrlsmf must still identify each previous hop from the outer header.
3. NRLSMF_TEST_CAPTURE_INNER_SRC=1 and NRLSMF_TEST_NO_GRE_OUTER_SOCKET=1:
   nrlsmf has no way to learn the previous hop. EM_ACKs then go to every
   peer and name no relay. Relays must still reach FORWARD, and no EM_ACK
   may be encapsulated toward a flow source.

Each phase checks, for every flow:
* on the wire (captured on u0): every EM_ACK's outer source is the
  acknowledging router, its outer destination is the flow's relay (every
  peer in phase 3), and it names that relay as its upstream address
  (0.0.0.0 in phase 3). No EM_ACK goes to a flow source or an overlay
  address. Routers without members send none.
* on each member router: the flow's selected source is gre1 with the
  relay's underlay as its upstream address (phases 1 and 2).
* on the relay: gre1 is FORWARD because of an EM_ACK, and its downstream
  relays are exactly the acknowledging routers (phases 1 and 2).
* traffic: members receive 8 pps; non-member hosts get at most 1 pps.
"""

from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import json
import sys
import time

sys.path.insert(0, str(script_dir()))
sys.path.insert(0, str(script_dir().parent))
from em_ack_wire import read_ack_capture
from em_ack_wire import start_ack_capture
from four_peer_hosts import HOST_IFACE
from four_peer_hosts import HOST_LANS
from four_peer_hosts import ROUTER_HOST_IFACE
from four_peer_hosts import count_overlay_mcast_pkts
from four_peer_hosts import enable_host_igmp
from four_peer_hosts import setup_host_lan
from four_peer_hosts import wait_igmp_group
from smf_cli import check_group_elastic
from smf_cli import cli_cmd
from smf_cli import parse_json_blob

ROUTERS = {
    "r0": {"underlay": "10.0.0.2", "overlay": "172.16.0.1"},
    "r1": {"underlay": "10.0.1.2", "overlay": "172.16.0.2"},
    "r2": {"underlay": "10.0.2.2", "overlay": "172.16.0.3"},
    "r3": {"underlay": "10.0.3.2", "overlay": "172.16.0.4"},
}
GRE_DEV = "gre1"
HOST_OF = {router: host for router, _, host, _ in HOST_LANS}
HOST_ADDR = {host: addr for _, _, host, addr in HOST_LANS}
ROUTER_OF = {host: router for router, _, host, _ in HOST_LANS}

FLOWS = {
    "239.0.0.1": {"source": "h0", "members": ("h1",)},
    "239.0.0.2": {"source": "h2", "members": ("h0", "h1", "h3")},
}
for _group, _flow in FLOWS.items():
    _flow["relay"] = ROUTER_OF[_flow["source"]]
    _flow["src_addr"] = HOST_ADDR[_flow["source"]]
    _flow["ackers"] = tuple(ROUTER_OF[h] for h in _flow["members"])

FLOW_SOURCES = {f["src_addr"] for f in FLOWS.values()}
OVERLAYS = {cfg["overlay"] for cfg in ROUTERS.values()}
UNDERLAY_OF = {name: cfg["underlay"] for name, cfg in ROUTERS.items()}
ROUTER_BY_UNDERLAY = {cfg["underlay"]: name for name, cfg in ROUTERS.items()}
ROUTER_BY_ADDR = {**ROUTER_BY_UNDERLAY,
                  **{cfg["overlay"]: name for name, cfg in ROUTERS.items()}}

ACK_WINDOW_S = 12
IDLE_MAX_PPS = 1
WINDOW_S = 3.0


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def inst(name):
    return f"smf-{name}"


def groups_details(node):
    raw = cli_cmd(node, "show groups details json", inst(node))
    try:
        return parse_json_blob(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def flow_row(rows, group, src_addr):
    for row in rows or ():
        if (isinstance(row, dict) and row.get("MCastAddr") == group and
                row.get("SrcAddr") == src_addr):
            return row
    return None


def selected_source(row):
    for src in (row or {}).get("Sources") or ():
        if src.get("Selected") is True:
            return src
    return None


def outbound(row, iface):
    for oif in (row or {}).get("Outbound") or ():
        if oif.get("Interface") == iface:
            return oif
    return None


def wait_relay_forward(relay, group, src_addr, timeout=30):
    """Poll the relay until gre1 is FORWARD for the flow."""
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        row = flow_row(groups_details(relay), group, src_addr)
        oif = outbound(row, GRE_DEV)
        if oif is not None and oif.get("FwdStatus") == "FORWARD":
            return row
        time.sleep(1)
    return row


def start_iperf_server(host, group):
    step(host, f"ip route replace {group}/32 dev {HOST_IFACE}")
    step(
        host,
        f"iperf -u -T 4 -i 1 -s -e -B {group}%{HOST_IFACE} "
        f"> iperf-{host}-{group}.log 2>&1 &",
    )


def start_iperf_client(host, group):
    step(host, f"ip route replace {group}/32 dev {HOST_IFACE}")
    step(
        host,
        f"iperf -u -T 16 -t 1000 -i 1 -b 8pps -l 1024 -e "
        f"-B {HOST_ADDR[host]} -c {group} &> iperf-{host}-client-{group}.log &",
    )
    wait_step(
        host,
        f"tail -n1 iperf-{host}-client-{group}.log",
        match="8 pps",
        desc=f"{host} sending {group} at 8 pps",
        timeout=30,
    )


def stop_nrlsmf():
    for name in ROUTERS:
        step(name, "pkill nrlsmf || true")
    for name in ROUTERS:
        wait_step(
            name,
            "pgrep -af nrlsmf || true",
            match="",
            desc=f"{name} nrlsmf stopped",
            timeout=15,
        )


def start_nrlsmf(phase, env):
    """Start elastic nrlsmf on every router with env (test hooks)."""
    for name, cfg in ROUTERS.items():
        if name in ("r0", "r2"):
            # Learn GRE inject destinations from kernel ip neigh.
            maps = f"map {GRE_DEV},{cfg['underlay']},dynamic"
        else:
            maps = " ".join(
                f"map {GRE_DEV},{cfg['underlay']},{ocfg['underlay']}"
                for other, ocfg in ROUTERS.items()
                if other != name
            )
        step(
            name,
            f"{env} nrlsmf debug 4 "
            f"instance {inst(name)} "
            f"add overlay,cf,{ROUTER_HOST_IFACE},{GRE_DEV} "
            f"layered {GRE_DEV} "
            f"{maps} "
            f"&> nrlsmf-elastic-ack-{phase}.log &",
        )
    for name in ROUTERS:
        wait_step(
            name,
            f'pgrep -af "nrlsmf.*instance {inst(name)}"',
            match=inst(name),
            desc=f"{name} nrlsmf running ({phase})",
            timeout=20,
        )
        wait_step(
            name,
            f'grep "regular group" nrlsmf-elastic-ack-{phase}.log',
            match="overlay",
            desc=f"{name} nrlsmf log shows overlay group ({phase})",
            timeout=20,
        )
    for name in ROUTERS:
        cli_cmd(name, "with-frr", inst(name))
        cli_cmd(name, "elastic overlay", inst(name))
        check_group_elastic(
            name, inst(name), "overlay", enabled=True,
            ifaces=(ROUTER_HOST_IFACE, GRE_DEV),
        )


def check_wire(phase, acks, relay_known):
    """Check every captured EM_ACK against the flow it acknowledges."""
    test_step(bool(acks), f"[{phase}] u0 captured EM_ACKs on the underlay", target="u0")
    seen = set()
    for ack in acks:
        group = ack["group"]
        flow = FLOWS.get(group)
        acker = ROUTER_BY_UNDERLAY.get(ack["outer_src"])
        desc = (f"EM_ACK {ack['outer_src']} -> {ack['outer_dst']} "
                f"for ({ack['source']}, {group}) upstream {ack['upstreams']}")
        if flow is None or acker is None:
            test_step(False, f"[{phase}] unexpected {desc}", target="u0")
            continue
        relay_ul = UNDERLAY_OF[flow["relay"]]
        test_step(
            ack["outer_dst"] not in FLOW_SOURCES,
            f"[{phase}] {desc}: GRE destination is not a flow source",
            target=acker,
        )
        test_step(
            ack["outer_dst"] not in OVERLAYS,
            f"[{phase}] {desc}: GRE destination is not an overlay address",
            target=acker,
        )
        test_step(
            ack["source"] == flow["src_addr"],
            f"[{phase}] {desc}: acknowledges the flow source {flow['src_addr']}",
            target=acker,
        )
        test_step(
            acker in flow["ackers"],
            f"[{phase}] {desc}: {acker} has members of {group}",
            target=acker,
        )
        if relay_known:
            test_step(
                ack["outer_dst"] == relay_ul,
                f"[{phase}] {desc}: GRE destination is relay {flow['relay']} ({relay_ul})",
                target=acker,
            )
            test_step(
                ack["upstreams"] == [relay_ul],
                f"[{phase}] {desc}: names relay {flow['relay']} ({relay_ul})",
                target=acker,
            )
        else:
            test_step(
                ack["outer_dst"] in ROUTER_BY_UNDERLAY and ack["outer_dst"] != ack["outer_src"],
                f"[{phase}] {desc}: GRE destination is a tunnel peer",
                target=acker,
            )
            test_step(
                ack["upstreams"] == ["0.0.0.0"],
                f"[{phase}] {desc}: names no relay (previous hop unknown)",
                target=acker,
            )
        seen.add((acker, group))
    for group, flow in FLOWS.items():
        for acker in flow["ackers"]:
            test_step(
                (acker, group) in seen,
                f"[{phase}] {acker} sent an EM_ACK for ({flow['src_addr']}, {group})",
                target=acker,
            )


def check_state(phase, relay_known):
    """Receiver and relay views of every flow must agree."""
    for group, flow in FLOWS.items():
        relay = flow["relay"]
        relay_ul = UNDERLAY_OF[relay]
        src_addr = flow["src_addr"]

        row = wait_relay_forward(relay, group, src_addr)
        oif = outbound(row, GRE_DEV)
        test_step(
            oif is not None and oif.get("FwdStatus") == "FORWARD",
            f"[{phase}] relay {relay} {GRE_DEV} FORWARD for ({src_addr}, {group}) "
            f"(got {oif.get('FwdStatus') if oif else None!r})",
            target=relay,
        )
        if oif is not None:
            test_step(
                oif.get("Why") == "ack",
                f"[{phase}] relay {relay} {GRE_DEV} forwards {group} because of EM_ACK "
                f"(got {oif.get('Why')!r})",
                target=relay,
            )
            if relay_known:
                # A downstream relay is the EM_ACK's IP source: the acking
                # router's overlay or tunnel local address.
                want = sorted(flow["ackers"])
                addrs = oif.get("DownstreamRelays") or []
                have = sorted(ROUTER_BY_ADDR.get(a, a) for a in addrs)
                test_step(
                    have == want,
                    f"[{phase}] relay {relay} {GRE_DEV} downstream relays for {group} "
                    f"are {want} (got {addrs})",
                    target=relay,
                )

        for acker in flow["ackers"]:
            arow = flow_row(groups_details(acker), group, src_addr)
            test_step(
                arow is not None,
                f"[{phase}] {acker} has flow ({src_addr}, {group})",
                target=acker,
            )
            if arow is None:
                continue
            test_step(
                arow.get("Ack") == "yes",
                f"[{phase}] {acker} acknowledges ({src_addr}, {group})",
                target=acker,
            )
            sel = selected_source(arow)
            test_step(
                sel is not None and sel.get("Interface") == GRE_DEV,
                f"[{phase}] {acker} selects {GRE_DEV} for ({src_addr}, {group}) "
                f"(got {sel!r})",
                target=acker,
            )
            if relay_known and sel is not None:
                test_step(
                    sel.get("UpstreamAddr") == relay_ul,
                    f"[{phase}] {acker} upstream for {group} is relay {relay} ({relay_ul}), "
                    f"not the flow source (got {sel.get('UpstreamAddr')!r})",
                    target=acker,
                )


def check_traffic(phase):
    for group, flow in FLOWS.items():
        for host in flow["members"]:
            wait_step(
                host,
                f"tail -n2 iperf-{host}-{group}.log",
                match="8 pps",
                desc=f"[{phase}] member {host} receives {group} at 8 pps",
                timeout=30,
            )
        non_members = [h for h in HOST_OF.values()
                       if h not in flow["members"] and h != flow["source"]]
        for host in non_members:
            n = count_overlay_mcast_pkts(step, host, group, window_s=WINDOW_S)
            pps = n / WINDOW_S
            test_step(
                pps <= IDLE_MAX_PPS,
                f"[{phase}] non-member {host} gets {group} at {pps:.1f} pps "
                f"(at most {IDLE_MAX_PPS})",
                target=host,
            )


def run_phase(phase, env, relay_known):
    section(f"[{phase}] Start elastic nrlsmf")
    stop_nrlsmf()
    start_ack_capture("u0", phase)
    start_nrlsmf(phase, env)

    section(f"[{phase}] Relays reach FORWARD; receivers and relays agree")
    check_state(phase, relay_known)

    section(f"[{phase}] Traffic follows the acknowledgements")
    check_traffic(phase)

    section(f"[{phase}] EM_ACKs on the wire")
    time.sleep(ACK_WINDOW_S)
    acks = read_ack_capture("u0", phase)
    check_wire(phase, acks, relay_known)


# ---------------------------------------------------------------------
# Underlay and overlay
# ---------------------------------------------------------------------

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
        if src != dst:
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
    step(
        name,
        f"ip link add name {GRE_DEV} type gre "
        f"local {cfg['underlay']} remote 0.0.0.0 ttl 64",
    )
    step(name, f"ip addr add {cfg['overlay']}/24 dev {GRE_DEV}")
    step(name, f"ip link set {GRE_DEV} multicast on")
    step(name, f"ip link set {GRE_DEV} up")
    for other, ocfg in ROUTERS.items():
        if other != name:
            step(
                name,
                f"ip neigh replace {ocfg['overlay']} lladdr {ocfg['underlay']} "
                f"nud permanent dev {GRE_DEV}",
            )
    wait_step(name, f"ip -br link show {GRE_DEV}", match="UP", desc=f"{name} {GRE_DEV} is UP")

for src, scfg in ROUTERS.items():
    for dst, dcfg in ROUTERS.items():
        if src != dst:
            wait_step(
                src,
                f"ping -c1 -W3 -I {scfg['overlay']} {dcfg['overlay']}",
                match="1 received",
                desc=f"{src} ping overlay {dst} ({dcfg['overlay']})",
                timeout=20,
            )

section("Host IGMP and application multicast")

enable_host_igmp(step, wait_step, ROUTERS)
for group, flow in FLOWS.items():
    for host in flow["members"]:
        start_iperf_server(host, group)
for group, flow in FLOWS.items():
    for host in flow["members"]:
        wait_igmp_group(wait_step, ROUTER_OF[host], group)
for group, flow in FLOWS.items():
    start_iperf_client(flow["source"], group)

# ---------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------

run_phase("normal", "", relay_known=True)
run_phase("inner-capture-src", "NRLSMF_TEST_CAPTURE_INNER_SRC=1", relay_known=True)
run_phase(
    "no-prev-hop",
    "NRLSMF_TEST_CAPTURE_INNER_SRC=1 NRLSMF_TEST_NO_GRE_OUTER_SOCKET=1",
    relay_known=False,
)

section("Cleanup")

for host in HOST_OF.values():
    step(host, "pkill iperf || true")
stop_nrlsmf()

test_step(True, "mGRE elastic acknowledgement mutest completed")
