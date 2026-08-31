"""Host-LAN helpers for the chained-clouds GRE/mGRE mutest.

Iperf is sourced on ha (off A) and received on ha2/hb2/hc2/hf so
nrlsmf CF is the first hop onto the overlay and the last hop off it
on each cloud.
"""

HOST_IFACE = "eth0"
ROUTER_HOST_IFACE = "eth1"
IPERF_TTL = "16"

# (router, router eth1 addr, host name, host addr)
HOST_LANS = (
    ("A", "192.168.55.1", "ha", "192.168.55.2"),
    ("A2", "192.168.56.1", "ha2", "192.168.56.2"),
    ("B2", "192.168.57.1", "hb2", "192.168.57.2"),
    ("C2", "192.168.58.1", "hc2", "192.168.58.2"),
    ("F", "192.168.59.1", "hf", "192.168.59.2"),
)

SOURCE_HOST = "ha"
SOURCE_HOST_ADDR = "192.168.55.2"
RECV_HOSTS = ("ha2", "hb2", "hc2", "hf")


def setup_host_lan(step, wait_step):
    """Bring up each listed router's host LAN and its application host."""
    for router, router_addr, host, host_addr in HOST_LANS:
        step(router, f"ethtool -K {ROUTER_HOST_IFACE} rx off tx off || true")
        wait_step(
            router,
            f"ip -br addr show dev {ROUTER_HOST_IFACE}",
            match=router_addr,
            desc=f"{router} {ROUTER_HOST_IFACE} address {router_addr}",
            timeout=30,
        )
        step(router, "sysctl -w net.ipv4.conf.all.mc_forwarding=0 || true")

        step(host, f"ethtool -K {HOST_IFACE} rx off tx off || true")
        step(host, f"ip addr add {host_addr}/24 dev {HOST_IFACE} || true")
        step(host, f"ip link set {HOST_IFACE} up")
        step(host, f"ip route replace 0.0.0.0/0 via {router_addr}")
        wait_step(
            host,
            f"ip -br addr show dev {HOST_IFACE}",
            match=host_addr,
            desc=f"{host} {HOST_IFACE} address {host_addr}",
            timeout=30,
        )
        wait_step(
            host,
            f"ping -c1 -W2 {router_addr}",
            match="1 received",
            desc=f"{host} reaches {router} on the host LAN",
            timeout=20,
        )


def start_overlay_mcast_servers(step, receivers, mcast):
    for name in receivers:
        step(name, f"ip route replace {mcast}/32 dev {HOST_IFACE}")
        step(
            name,
            f"iperf -u -T 4 -i 1 -s -e -B {mcast}%{HOST_IFACE} "
            f"> iperf-{name}-server.log 2>&1 &",
        )


def start_host_mcast_client(step, wait_step, mcast):
    step(SOURCE_HOST, f"ip route replace {mcast}/32 dev {HOST_IFACE}")
    step(
        SOURCE_HOST,
        f"iperf -u -T {IPERF_TTL} -t 1000 -i 1 -b 8pps -l 1024 -e "
        f"-B {SOURCE_HOST_ADDR} -c {mcast} &> iperf-{SOURCE_HOST}-client.log &",
    )
    wait_step(
        SOURCE_HOST,
        f"tail -n1 iperf-{SOURCE_HOST}-client.log",
        match="8 pps",
        desc="ha sending application multicast at 8 pps",
        timeout=30,
    )


def wait_overlay_mcast_receivers(wait_step, receivers):
    for name in receivers:
        wait_step(
            name,
            f'grep "8 pps" iperf-{name}-server.log',
            match="8 pps",
            desc=f"{name} receiving application multicast at 8 pps",
            timeout=20,
        )


def cleanup_iperf(step, receivers):
    step(SOURCE_HOST, "pkill iperf || true")
    for name in receivers:
        step(name, "pkill iperf || true")
