"""Example: nrlsmf Elastic Multicast (EM) routing between two host LANs.

Topology (shared with other tests in this directory):

  h0 -- lan0 --\\
                 r0
  h1 -- lan1 --/

What "elastic" means here
----------------------------
`elastic <group>` overlays Elastic Multicast (EM) routing on top of an
existing flooding group -- here, the same `cf` group ("net") used in
mutest_smf_cf.py. Instead of nrlsmf blindly flooding every multicast
packet it sees, EM manages flows more deliberately: rather than
matching iperf's full sending rate, this build's EM flow control uses a
token-bucket that limits an established flow. Where mutest_smf_cf.py's
classical flooding relayed h0's full 8 pps sending rate through to h1
unchanged, this test sends the exact same 8 pps flow and expects it to
arrive at h1 rate-limited to 1 KByte/sec instead -- direct evidence
that EM's flow control, not blind flooding, is what's forwarding this
traffic.

What this example covers
-------------------------
* nrlsmf started with `add net,cf,eth0,eth1 elastic net` on r0 --
  the same `cf` group as mutest_smf_cf.py, with EM enabled on it.
* The same "net" / "push:eth0" / "push:eth1" group log lines as the
  plain `cf` case (EM sits on top of the same group structure).
* The same h0 -> 239.0.0.1 -> h1 multicast flow as the other tests
  here, but this time checked for EM's rate-limited delivery instead
  of the full sending rate.

See mutest_smf_cli.py (CLI sanity checks, no traffic), mutest_smf_merge.py
(forced two-interface relay), mutest_smf_cf.py (classical flooding,
same group without EM), and mutest_smf_advertise.py (EM_ADV control
messages) for the other nrlsmf modes on this same topology.
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
from onehop_hosts import wait_mcast_receiver

MCAST_GROUP = "239.0.0.1"

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

section("Start nrlsmf with Elastic Multicast on r0")

step(
    "r0",
    "nrlsmf debug 4 add net,cf,eth0,eth1 elastic net &> nrlsmf-elastic.log &",
)

wait_step(
    "r0",
    'pgrep -af "nrlsmf.*elastic net"',
    match="elastic net",
    desc="nrlsmf is started with elastic group net",
)

# Same group structure as plain `cf` (mutest_smf_cf.py) -- EM overlays
# on top of it rather than replacing it.
wait_step(
    "r0",
    'grep "regular group" nrlsmf-elastic.log',
    match='"net" eth0,eth1',
    desc='nrlsmf-elastic.log contains group "net" eth0,eth1',
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-elastic.log',
    match='"push:eth0" eth0',
    desc='nrlsmf-elastic.log contains the implicit "push:eth0" sub-group',
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-elastic.log',
    match='"push:eth1" eth1',
    desc='nrlsmf-elastic.log contains the implicit "push:eth1" sub-group',
)

section("Multicast from h0 is rate-limited by EM before reaching h1")

setup_mcast_route(step)
start_mcast_server(step)
start_mcast_client(step, wait_step)
# Delivery is rate-limited by EM's flow control to 1.00 KBytes/sec
# rather than matching h0's full 8 pps sending rate.
wait_mcast_receiver(
    wait_step,
    match="1.00 KBytes",
    desc="h1 receiving 239.0.0.1 rate-limited by EM to 1 pps",
    timeout=30,
)

section("Cleanup")

cleanup_iperf(step)
step("r0", "pkill nrlsmf || true")

wait_step(
    "r0",
    'pgrep -af "nrlsmf" || true',
    match="",
    desc="r0 nrlsmf stopped",
)

test_step(True, "nrlsmf Elastic Multicast mutest completed")
