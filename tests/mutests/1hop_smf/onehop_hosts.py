"""Host multicast helpers for the 1hop_smf mutests.

h0 sends 239.0.0.1; h1 receives. nrlsmf on r0 is the relay.
"""

HOST_IFACE = "eth0"
MCAST_GROUP = "239.0.0.1"
H0_ADDR = "10.0.0.2"
IPERF_TTL = "16"


def setup_mcast_route(step):
    for name in ("h0", "h1"):
        step(name, f"ip route replace {MCAST_GROUP}/32 dev {HOST_IFACE}")


def start_mcast_server(step):
    step(
        "h1",
        f"iperf -u -T 4 -i 1 -s -e -B {MCAST_GROUP}%{HOST_IFACE} "
        "> iperf-server.log 2>&1 &",
    )


def start_mcast_client(step, wait_step):
    step(
        "h0",
        f"iperf -u -T {IPERF_TTL} -t 1000 -i 1 -b 8pps -l 1024 -e "
        f"-B {H0_ADDR} -c {MCAST_GROUP} &> iperf-client.log &",
    )
    wait_step(
        "h0",
        'grep "8 pps" iperf-client.log',
        match="8 pps",
        desc=f"h0 sending {MCAST_GROUP} at 8 pps",
        timeout=30,
    )


def wait_mcast_receiver(wait_step, match="8 pps", desc=None, timeout=20):
    if desc is None:
        desc = f"h1 receiving {MCAST_GROUP} at {match}"
    wait_step(
        "h1",
        f'grep "{match}" iperf-server.log',
        match=match,
        desc=desc,
        timeout=timeout,
    )


def cleanup_iperf(step):
    step("h0", "pkill iperf || true")
    step("h1", "pkill iperf || true")
