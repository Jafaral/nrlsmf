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
from chained_hosts import start_host_mcast_client
from chained_hosts import start_overlay_mcast_servers
from chained_hosts import wait_overlay_mcast_receivers
from kernel_compat import min_kernel_version
from smf_cli import check_common_show
from smf_cli import check_show_neighbors
from smf_cli import check_show_tunnel
from chained_build import C0_PEERS
from chained_build import C1_PEERS
from chained_build import C2_PEERS
from chained_build import C4_PEERS
from chained_build import GRE_C0
from chained_build import GRE_C1
from chained_build import GRE_C2
from chained_build import GRE_C4
from chained_build import GRE_P2P
from chained_build import OVERLAY_MCAST
from chained_build import P2P_PEERS
from chained_build import UNDERLAY_MCAST_C2
from chained_build import build_network
from chained_build import start_nrlsmf

if min_kernel_version((5, 0)):
    return "skip"


build_network()
start_nrlsmf()

section("nrlsmf --cli show tunnel / neighbors (json, all five GRE modes)")

# cloud0 static NBMA: A learns from neigh; A2 has explicit maps.
check_common_show("A", "smf-A", group_name="overlay", ifaces=("eth1", GRE_C0))
check_show_tunnel(
    "A", "smf-A", GRE_C0,
    local=C0_PEERS["A"]["underlay"],
    remotes=[C0_PEERS["A2"]["underlay"], C0_PEERS["B"]["underlay"]],
    overlay_ip=C0_PEERS["A"]["overlay"],
)
check_show_neighbors(
    "A", "smf-A", GRE_C0,
    remotes=[C0_PEERS["A2"]["underlay"], C0_PEERS["B"]["underlay"]],
    neighbor_ips=[C0_PEERS["A2"]["overlay"], C0_PEERS["B"]["overlay"]],
    min_count=2,
)
check_show_tunnel(
    "A2", "smf-A2", GRE_C0,
    local=C0_PEERS["A2"]["underlay"],
    remotes=[C0_PEERS["A"]["underlay"], C0_PEERS["B"]["underlay"]],
    overlay_ip=C0_PEERS["A2"]["overlay"],
    want_c=True,
)
check_show_neighbors(
    "A2", "smf-A2", GRE_C0,
    remotes=[C0_PEERS["A"]["underlay"], C0_PEERS["B"]["underlay"]],
    neighbor_ips=[C0_PEERS["A"]["overlay"], C0_PEERS["B"]["overlay"]],
    min_count=2,
    want_c=True,
)

# cloud1 NHRP: hub B2 sees both spokes.
check_show_neighbors(
    "B2", "smf-B2", GRE_C1,
    remotes=[C1_PEERS["B"]["underlay"], C1_PEERS["C"]["underlay"]],
    neighbor_ips=[C1_PEERS["B"]["overlay"], C1_PEERS["C"]["overlay"]],
    min_count=2,
)
check_show_neighbors(
    "B", "smf-B", GRE_C1,
    remotes=[C1_PEERS["B2"]["underlay"]],
    neighbor_ips=[C1_PEERS["B2"]["overlay"]],
    min_count=1,
)

# cloud2 multicast-underlay remote.
check_show_tunnel(
    "C2", "smf-C2", GRE_C2,
    local=C2_PEERS["C2"]["underlay"],
    remotes=[UNDERLAY_MCAST_C2],
    overlay_ip=C2_PEERS["C2"]["overlay"],
    want_c=False,
)

# cloud3 P2P: kernel-learned single remote, no map.
check_show_tunnel(
    "D", "smf-D", GRE_P2P,
    local=P2P_PEERS["D"]["underlay"],
    remotes=[P2P_PEERS["E"]["underlay"]],
    overlay_ip=P2P_PEERS["D"]["overlay"],
    want_c=False,
)
check_show_neighbors(
    "D", "smf-D", GRE_P2P,
    remotes=[P2P_PEERS["E"]["underlay"]],
    min_count=1,
)

# cloud4 external GRE: explicit maps, no neigh table.
check_show_tunnel(
    "F", "smf-F", GRE_C4,
    local=C4_PEERS["F"]["underlay"],
    remotes=[C4_PEERS["E"]["underlay"], C4_PEERS["E2"]["underlay"]],
    overlay_ip=C4_PEERS["F"]["overlay"],
    want_c=True,
)
check_show_neighbors(
    "F", "smf-F", GRE_C4,
    remotes=[C4_PEERS["E"]["underlay"], C4_PEERS["E2"]["underlay"]],
    min_count=2,
    want_c=True,
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
