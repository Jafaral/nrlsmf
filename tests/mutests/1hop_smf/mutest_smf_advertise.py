"""Example: nrlsmf Elastic Multicast advertise mode (EM_ADV control messages).

Topology (shared with other tests in this directory):

  h0 -- lan0 --\\
                 r0
  h1 -- lan1 --/

What "advertise" means here
-------------------------------
mutest_smf_elastic.py showed Elastic Multicast's data-plane behavior
(rate-limited delivery instead of blind flooding). This test checks its
control plane instead: with `advertise` enabled alongside `elastic`,
nrlsmf periodically emits EM_ADV ("advertisement") messages -- sent to
the well-known Elastic Multicast control address 224.0.0.55, UDP port
5555 -- announcing multicast flow/reachability information so
downstream EM-aware nodes can discover and route toward active flows.
This test starts the same h0 -> 239.0.0.1 flow so EM has an active
flow to announce, then captures EM_ADV packets with tcpdump on h1's
LAN.

What this example covers
-------------------------
* nrlsmf started with `advertise add net,cf,eth0,eth1 elastic net` on
  r0 -- the same EM-enabled group as mutest_smf_elastic.py, with
  advertisement enabled.
* An iperf UDP multicast flow from h0 so EM has an active flow to
  advertise.
* tcpdump on h1 capturing EM_ADV packets (UDP, destination
  224.0.0.55:5555) to confirm the control-plane advertisement traffic
  is actually being sent.

See mutest_smf_cli.py (CLI sanity checks, no traffic), mutest_smf_merge.py
(forced two-interface relay), mutest_smf_cf.py (classical flooding),
and mutest_smf_elastic.py (EM data-plane rate limiting) for the other
nrlsmf modes on this same topology.
"""

from munet.mutest.userapi import script_dir
from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

import sys

sys.path.insert(0, str(script_dir()))
from onehop_hosts import cleanup_iperf
from onehop_hosts import setup_mcast_route
from onehop_hosts import start_mcast_client
from onehop_hosts import start_mcast_server

EM_ADV_ADDR = "224.0.0.55"
EM_ADV_PORT = "5555"

section("Verify interfaces are ready")

for node in ("h0", "h1", "r0"):
    step(node, "ethtool -K eth0 rx off tx off")

step("r0", "ethtool -K eth1 rx off tx off")

wait_step(
    "h0",
    "ip -br addr show dev eth0",
    match="10.0.0.2/24",
    desc="h0 has address 10.0.0.2/24",
)
wait_step(
    "h1",
    "ip -br addr show dev eth0",
    match="10.0.1.2/24",
    desc="h1 has address 10.0.1.2/24",
)

section("Start nrlsmf with Elastic Multicast advertise mode on r0")

step(
    "r0",
    "nrlsmf debug 4 advertise add net,cf,eth0,eth1 elastic net "
    "&> nrlsmf-advertise.log &",
)

wait_step(
    "r0",
    'pgrep -af "nrlsmf.*advertise.*elastic net"',
    match="advertise",
    desc="nrlsmf is started with advertise + elastic group net",
)

wait_step(
    "r0",
    'grep "regular group" nrlsmf-advertise.log',
    match='"net" eth0,eth1',
    desc='nrlsmf-advertise.log contains group "net" eth0,eth1',
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-advertise.log',
    match='"push:eth0" eth0',
    desc='nrlsmf-advertise.log contains the implicit "push:eth0" sub-group',
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-advertise.log',
    match='"push:eth1" eth1',
    desc='nrlsmf-advertise.log contains the implicit "push:eth1" sub-group',
)

section("Capture EM_ADV control messages on h1")

step(
    "h1",
    f"tcpdump -lnni eth0 'udp dst port {EM_ADV_PORT} and dst {EM_ADV_ADDR}' "
    "&> tcpdump-emadv.log &",
)

setup_mcast_route(step)
start_mcast_server(step)
start_mcast_client(step, wait_step)

wait_step(
    "h1",
    f"grep -m1 '{EM_ADV_ADDR}.{EM_ADV_PORT}' tcpdump-emadv.log",
    match=f"{EM_ADV_ADDR}.{EM_ADV_PORT}",
    desc="EM_ADV packets seen on h1 eth0 in advertise mode",
    timeout=30,
)

section("Cleanup")

cleanup_iperf(step)
step("h1", "pkill tcpdump || true")
step("r0", "pkill nrlsmf || true")

wait_step(
    "r0",
    'pgrep -af "nrlsmf" || true',
    match="",
    desc="r0 nrlsmf stopped",
)

test_step(True, "nrlsmf advertise mode mutest completed")
