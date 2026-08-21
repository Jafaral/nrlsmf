"""Example: nrlsmf classical flooding (`cf`) between two host LANs.

Topology (shared with other tests in this directory):

  h0 -- lan0 --\\
                 r0
  h1 -- lan1 --/

What "classical flooding" means here
---------------------------------------
`cf <ifaceList>` (Classical Flooding) is SMF's baseline relay
algorithm: flood received multicast out every other interface in the
group, with duplicate-packet detection to avoid retransmission storms.
Here it's invoked via the more general `add <group>,cf,<ifaceList>`
form, which creates a named interface group ("net") using the `cf`
relay algorithm.

One detail worth calling out: nrlsmf's log shows not just the "net"
group itself but also two automatically-created "push:eth0" and
"push:eth1" sub-groups -- one per member interface. This reflects how
nrlsmf's internal bookkeeping decomposes a named flooding group into
per-interface push relationships; it's not something you configure
directly here; it's what `cf` sets up for you.

What this example covers
-------------------------
* nrlsmf started with `add net,cf,eth0,eth1` on r0, and the implicit
  push:eth0 / push:eth1 sub-groups that come with it.
* The same multicast reachability check as mutest_smf_merge.py (h0 ->
  239.0.0.1 -> h1), confirming `cf` relays traffic just as `merge` did,
  as expected for this simple two-interface case.

See mutest_smf_cli.py (CLI sanity checks, no traffic), mutest_smf_merge.py
(forced two-interface relay), mutest_smf_elastic.py (elastic/rate-limited
multicast), and mutest_smf_advertise.py (EM_ADV control messages) for
the other nrlsmf modes on this same topology.
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

section("Start nrlsmf classical flooding on r0")

step("r0", "nrlsmf debug 4 add net,cf,eth0,eth1 &> nrlsmf-cf.log &")

wait_step(
    "r0",
    'pgrep -af "nrlsmf.*net,cf,eth0,eth1"',
    match="net,cf,eth0,eth1",
    desc="nrlsmf is started with classical flooding group",
)

wait_step(
    "r0",
    'grep "regular group" nrlsmf-cf.log',
    match='"net" eth0,eth1',
    desc='nrlsmf-cf.log contains group "net" eth0,eth1',
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-cf.log',
    match='"push:eth0" eth0',
    desc='nrlsmf-cf.log contains the implicit "push:eth0" sub-group',
)
wait_step(
    "r0",
    'grep "regular group" nrlsmf-cf.log',
    match='"push:eth1" eth1',
    desc='nrlsmf-cf.log contains the implicit "push:eth1" sub-group',
)

section("Multicast from h0 reaches h1 via classical flooding on r0")

setup_mcast_route(step)
start_mcast_server(step)
start_mcast_client(step, wait_step)
wait_mcast_receiver(
    wait_step,
    match="8 pps",
    desc=f"h1 receiving {MCAST_GROUP} at full rate of 8 pps",
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

test_step(True, "nrlsmf classical flooding mutest completed")
