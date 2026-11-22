"""Elastic multicast acknowledgements hop by hop across all five GRE modes.

Topology (see mutest_chained_clouds.py):

    ha   ha2         hb2        hc2                        hf
    |     |          |          |                          |
    |     A2         B2         C2                   E2    |
    |     |          |          |                    |     |
    A -- u0 -- B -- u1 -- C -- u2 -- D -- u3 -- E -- u4 -- F

  cloud0 (u0): A, A2, B           -- static NBMA mGRE
  cloud1 (u1): B, B2 (hub/NHS), C  -- NHRP-resolved mGRE
  cloud2 (u2): C, C2, D            -- multicast-underlay mGRE
  cloud3 (u3): D, E                -- point-to-point GRE
  cloud4 (u4): E, E2, F            -- external (metadata) GRE

One flow, 239.0.0.1 from ha. Members: hb2, hc2, hf. ha2 is not a member.

With elastic multicast every router that forwards the flow downstream,
or has members, acknowledges it (EM_ACK) to its previous hop on the
cloud it receives the flow from. On every cloud the previous hop is a
relay, never the flow source, and on cloud1 it is not even the router
that brought the flow into the cloud: spokes only reach each other
through the hub, so C's previous hop is B2.

Expected acknowledgements, per cloud (acker -> relay):
  cloud0  B -> A
  cloud1  B2 -> B, C -> B2
  cloud2  C2 -> C, D -> C   (sent to the underlay group; only C applies them)
  cloud3  E -> D
  cloud4  F -> E

Phases
------
1. Normal operation.
2. NRLSMF_TEST_CAPTURE_INNER_SRC=1: the packet capture reports the inner
   IP source as a packet's source on GRE devices, as some kernels do.

Each phase checks, per cloud:
* on the wire (captured on the cloud's underlay router): every EM_ACK's
  outer source is an expected acker, its outer destination is that
  acker's relay (the underlay group on cloud2), it names that relay as
  its upstream address, and never goes to the flow source.
* on each acker: the flow's selected source is the cloud's GRE device
  with the relay's underlay as upstream address.
* on each relay: the cloud's GRE device is FORWARD because of EM_ACK,
  and its downstream relays are exactly the expected ackers.
* traffic: members receive 8 pps; ha2 gets at most 1 pps.
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
from chained_build import C0_PEERS
from chained_build import C1_PEERS
from chained_build import C2_PEERS
from chained_build import C4_PEERS
from chained_build import GRE_C0
from chained_build import GRE_C1
from chained_build import GRE_C2
from chained_build import GRE_C4
from chained_build import GRE_P2P
from chained_build import OVERLAY_ROUTERS
from chained_build import P2P_PEERS
from chained_build import UNDERLAY_MCAST_C2
from chained_build import build_network
from chained_build import start_nrlsmf
from chained_hosts import HOST_IFACE
from chained_hosts import HOST_LANS
from chained_hosts import ROUTER_HOST_IFACE
from chained_hosts import SOURCE_HOST
from chained_hosts import SOURCE_HOST_ADDR
from em_ack_wire import read_ack_capture
from em_ack_wire import start_ack_capture
from kernel_compat import min_kernel_version
from smf_cli import cli_cmd
from smf_cli import parse_json_blob

GROUP = "239.0.0.1"
MEMBERS = ("hb2", "hc2", "hf")
NON_MEMBERS = ("ha2",)
HOST_ROUTERS = tuple(router for router, _, _, _ in HOST_LANS)
ROUTER_OF = {host: router for router, _, host, _ in HOST_LANS}

CLOUDS = (
    {"name": "cloud0", "underlay": "u0", "dev": GRE_C0, "peers": C0_PEERS,
     "acks": {"B": "A"}},
    {"name": "cloud1", "underlay": "u1", "dev": GRE_C1, "peers": C1_PEERS,
     "acks": {"B2": "B", "C": "B2"}},
    {"name": "cloud2", "underlay": "u2", "dev": GRE_C2, "peers": C2_PEERS,
     "acks": {"C2": "C", "D": "C"}, "group": UNDERLAY_MCAST_C2},
    {"name": "cloud3", "underlay": "u3", "dev": GRE_P2P, "peers": P2P_PEERS,
     "acks": {"E": "D"}},
    {"name": "cloud4", "underlay": "u4", "dev": GRE_C4, "peers": C4_PEERS,
     "acks": {"F": "E"}},
)

ACK_WINDOW_S = 12
IDLE_MAX_PPS = 1
WINDOW_S = 3.0


def inst(name):
    return f"smf-{name}"


def groups_details(node):
    raw = cli_cmd(node, "show groups details json", inst(node))
    try:
        return parse_json_blob(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def flow_row(node):
    for row in groups_details(node) or ():
        if (isinstance(row, dict) and row.get("MCastAddr") == GROUP and
                row.get("SrcAddr") == SOURCE_HOST_ADDR):
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


def wait_downstream(relay, dev, want, by_addr, timeout=40):
    """Poll until the relay is FORWARD because of EM_ACK and lists want.

    The first acknowledgement moves the interface to FORWARD. On a
    multicast underlay a second member can acknowledge after that, so
    the downstream list is not complete at the moment FORWARD appears.
    """
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        row = flow_row(relay)
        oif = outbound(row, dev)
        if (oif is not None and oif.get("FwdStatus") == "FORWARD" and
                oif.get("Why") == "ack"):
            addrs = oif.get("DownstreamRelays") or []
            have = sorted(by_addr.get(a, a) for a in addrs)
            if have == want:
                return row
        time.sleep(1)
    return row


def wait_acker(acker, dev, relay_ul, timeout=40):
    """Poll until acker has the flow selected from relay_ul on dev."""
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        row = flow_row(acker)
        sel = selected_source(row)
        if (row is not None and row.get("Ack") == "yes" and
                sel is not None and sel.get("Interface") == dev and
                sel.get("UpstreamAddr") == relay_ul):
            return row
        time.sleep(1)
    return row


def stop_overlay_nrlsmf():
    # u2 keeps its underlay multicast stand-in running.
    for name in OVERLAY_ROUTERS:
        step(name, "pkill nrlsmf || true")
    for name in OVERLAY_ROUTERS:
        wait_step(
            name,
            "pgrep -af nrlsmf || true",
            match="",
            desc=f"{name} nrlsmf stopped",
            timeout=15,
        )


def enable_elastic():
    for name in HOST_ROUTERS:
        cli_cmd(name, "with-frr", inst(name))
    for name in OVERLAY_ROUTERS:
        cli_cmd(name, "elastic overlay", inst(name))


def check_state(phase):
    for cloud in CLOUDS:
        dev = cloud["dev"]
        peers = cloud["peers"]
        by_addr = {}
        for name, cfg in peers.items():
            by_addr[cfg["underlay"]] = name
            by_addr[cfg["overlay"]] = name
        relays = sorted(set(cloud["acks"].values()))
        for relay in relays:
            want = sorted(a for a, r in cloud["acks"].items() if r == relay)
            row = wait_downstream(relay, dev, want, by_addr)
            oif = outbound(row, dev)
            test_step(
                oif is not None and oif.get("FwdStatus") == "FORWARD",
                f"[{phase}] {cloud['name']} relay {relay} {dev} FORWARD "
                f"(got {oif.get('FwdStatus') if oif else None!r})",
                target=relay,
            )
            if oif is None:
                continue
            test_step(
                oif.get("Why") == "ack",
                f"[{phase}] {cloud['name']} relay {relay} {dev} forwards because of EM_ACK "
                f"(got {oif.get('Why')!r})",
                target=relay,
            )
            addrs = oif.get("DownstreamRelays") or []
            have = sorted(by_addr.get(a, a) for a in addrs)
            test_step(
                have == want,
                f"[{phase}] {cloud['name']} relay {relay} {dev} downstream relays are "
                f"{want} (got {addrs})",
                target=relay,
            )
        for acker, relay in cloud["acks"].items():
            relay_ul = peers[relay]["underlay"]
            row = wait_acker(acker, dev, relay_ul)
            test_step(row is not None, f"[{phase}] {acker} has the flow", target=acker)
            if row is None:
                continue
            test_step(
                row.get("Ack") == "yes",
                f"[{phase}] {acker} acknowledges the flow",
                target=acker,
            )
            sel = selected_source(row)
            test_step(
                sel is not None and sel.get("Interface") == dev and
                sel.get("UpstreamAddr") == relay_ul,
                f"[{phase}] {cloud['name']} {acker} selects {dev} from relay {relay} "
                f"({relay_ul}), not the flow source (got {sel!r})",
                target=acker,
            )


def check_wire(phase, cloud, acks):
    peers = cloud["peers"]
    by_underlay = {cfg["underlay"]: name for name, cfg in peers.items()}
    test_step(bool(acks), f"[{phase}] {cloud['underlay']} captured EM_ACKs on {cloud['name']}",
              target=cloud["underlay"])
    seen = set()
    for ack in acks:
        acker = by_underlay.get(ack["outer_src"])
        desc = (f"{cloud['name']} EM_ACK {ack['outer_src']} -> {ack['outer_dst']} "
                f"for ({ack['source']}, {ack['group']}) upstream {ack['upstreams']}")
        relay = cloud["acks"].get(acker)
        test_step(
            relay is not None,
            f"[{phase}] {desc}: sent by an expected acker",
            target=cloud["underlay"],
        )
        if relay is None:
            continue
        relay_ul = peers[relay]["underlay"]
        want_dst = cloud.get("group", relay_ul)
        test_step(
            ack["group"] == GROUP and ack["source"] == SOURCE_HOST_ADDR,
            f"[{phase}] {desc}: acknowledges ({SOURCE_HOST_ADDR}, {GROUP})",
            target=acker,
        )
        test_step(
            ack["outer_dst"] != SOURCE_HOST_ADDR,
            f"[{phase}] {desc}: GRE destination is not the flow source",
            target=acker,
        )
        test_step(
            ack["outer_dst"] == want_dst,
            f"[{phase}] {desc}: GRE destination is {want_dst} (relay {relay})",
            target=acker,
        )
        test_step(
            ack["upstreams"] == [relay_ul],
            f"[{phase}] {desc}: names relay {relay} ({relay_ul})",
            target=acker,
        )
        seen.add(acker)
    for acker, relay in cloud["acks"].items():
        test_step(
            acker in seen,
            f"[{phase}] {cloud['name']} {acker} sent an EM_ACK to {relay}",
            target=acker,
        )


def count_pkts(host):
    raw = step(
        host,
        f"timeout {WINDOW_S} tcpdump -nn -l -i {HOST_IFACE} host {GROUP} 2>/dev/null "
        f"| grep -c {GROUP} || true",
    )
    for tok in str(raw).split():
        if tok.isdigit():
            return int(tok)
    return 0


def check_traffic(phase):
    for host in MEMBERS:
        wait_step(
            host,
            f"tail -n2 iperf-{host}-server.log",
            match="8 pps",
            desc=f"[{phase}] member {host} receives {GROUP} at 8 pps",
            timeout=30,
        )
    for host in NON_MEMBERS:
        pps = count_pkts(host) / WINDOW_S
        test_step(
            pps <= IDLE_MAX_PPS,
            f"[{phase}] non-member {host} gets {GROUP} at {pps:.1f} pps "
            f"(at most {IDLE_MAX_PPS})",
            target=host,
        )


def run_phase(phase, env):
    section(f"[{phase}] Start elastic nrlsmf on every overlay router")
    stop_overlay_nrlsmf()
    for cloud in CLOUDS:
        start_ack_capture(cloud["underlay"], f"{phase}-{cloud['name']}")
    start_nrlsmf(env=env, tag=f"-{phase}")
    enable_elastic()

    section(f"[{phase}] Every relay reaches FORWARD; ackers and relays agree")
    check_state(phase)

    section(f"[{phase}] Traffic follows the acknowledgements")
    check_traffic(phase)

    section(f"[{phase}] EM_ACKs on the wire, cloud by cloud")
    time.sleep(ACK_WINDOW_S)
    for cloud in CLOUDS:
        acks = read_ack_capture(cloud["underlay"], f"{phase}-{cloud['name']}")
        check_wire(phase, cloud, acks)


if min_kernel_version((5, 0)):
    return "skip"

build_network()

section("Host IGMP; members join, ha sends")

for name in HOST_ROUTERS:
    # FRR serves IGMP from pimd, which these routers do not start by
    # default. Detach its stdio so the step returns once it daemonizes.
    step(name, "pgrep -x pimd >/dev/null || "
               "/usr/lib/frr/pimd -d </dev/null >/dev/null 2>&1")
    wait_step(name, "pgrep -x pimd", match="[0-9]+", desc=f"{name} pimd is running",
              timeout=20)
    step(
        name,
        "vtysh -c 'configure terminal' "
        f"-c 'interface {ROUTER_HOST_IFACE}' -c 'ip igmp'",
    )
for host in MEMBERS:
    step(host, f"ip route replace {GROUP}/32 dev {HOST_IFACE}")
    step(
        host,
        f"iperf -u -T 4 -i 1 -s -e -B {GROUP}%{HOST_IFACE} "
        f"> iperf-{host}-server.log 2>&1 &",
    )
for host in MEMBERS:
    wait_step(
        ROUTER_OF[host],
        "vtysh -c 'show ip igmp groups'",
        match=GROUP,
        desc=f"{ROUTER_OF[host]} FRR IGMP has {GROUP}",
        timeout=30,
    )
step(SOURCE_HOST, f"ip route replace {GROUP}/32 dev {HOST_IFACE}")
step(
    SOURCE_HOST,
    f"iperf -u -T 16 -t 1000 -i 1 -b 8pps -l 1024 -e "
    f"-B {SOURCE_HOST_ADDR} -c {GROUP} &> iperf-{SOURCE_HOST}-client.log &",
)
wait_step(
    SOURCE_HOST,
    f"tail -n1 iperf-{SOURCE_HOST}-client.log",
    match="8 pps",
    desc=f"{SOURCE_HOST} sending {GROUP} at 8 pps",
    timeout=30,
)

run_phase("normal", "")
run_phase("inner-capture-src", "NRLSMF_TEST_CAPTURE_INNER_SRC=1")

section("Cleanup")

step(SOURCE_HOST, "pkill iperf || true")
for host in MEMBERS:
    step(host, "pkill iperf || true")
for name in OVERLAY_ROUTERS + ("u2",):
    step(name, "pkill nrlsmf || true")

test_step(True, "chained five-mode elastic acknowledgement mutest completed")
