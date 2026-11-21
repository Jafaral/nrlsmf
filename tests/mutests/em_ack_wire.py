"""Capture and decode Elastic Multicast acknowledgements (EM_ACK) on the wire.

An EM_ACK sent on a GRE tunnel is a UDP packet (port 5555) inside a GRE
packet. The outer GRE destination decides which tunnel neighbor gets it;
the upstream address inside the EM_ACK names the relay it is meant for.
These helpers capture GRE on an underlay node and decode both, so a test
can check that each EM_ACK went to the previous hop of its flow (and never
to the flow source).

EM_ACK layout (IPv4 addresses):
    0  type (1 = ACK)        1  length (32-bit words)
    2  reserved (2 bytes)
    4  flags (high 4 bits) | address type (low 4 bits, 1 = IPv4)
    5  upstream count (high 4 bits) | upstream address type (low 4 bits)
    6  protocol              7  traffic class
    8  flow destination (group)
    12 flow source (when the SOURCE flag 0x01 is set)
    .. upstream addresses
"""

import ipaddress
import re

from munet.mutest.userapi import step

ELASTIC_PORT = 5555
# Small GRE packets only: EM_ACKs are well under 200 bytes, data is not.
_FILTER = "proto gre and ip[2:2] < 200"
_HEX_LINE = re.compile(r"^\s+0x[0-9a-f]+:\s+((?:[0-9a-f]{2,4}\s?)+)")


def start_ack_capture(node, tag, iface="any"):
    """Start capturing GRE on ``node``. Pair with ``read_ack_capture``.

    tcpdump prints hex to stdout and the shell writes the file: tcpdump
    itself may not be allowed to write capture files (AppArmor).
    """
    step(node, f"pkill -f 'tcpdump.*{_FILTER}' || true")
    step(node, f"rm -f em-ack-{tag}.txt")
    step(
        node,
        f"tcpdump -i {iface} -nn -l -x -s 256 '{_FILTER}' "
        f"> em-ack-{tag}.txt 2> em-ack-{tag}.log &",
    )


def _ip(raw):
    return str(ipaddress.IPv4Address(bytes(raw)))


def decode_em_ack(pkt):
    """Decode one outer IPv4/GRE packet. Returns a dict for an EM_ACK, else None."""
    if len(pkt) < 20 or (pkt[0] >> 4) != 4 or pkt[9] != 47:
        return None
    ihl = 4 * (pkt[0] & 0x0F)
    gre = pkt[ihl:]
    if len(gre) < 4 or (gre[1] & 0x07) != 0 or gre[2:4] != b"\x08\x00":
        return None
    glen = 4 + 4 * sum(1 for bit in (0x80, 0x20, 0x10) if gre[0] & bit)
    inner = gre[glen:]
    if len(inner) < 20 or (inner[0] >> 4) != 4 or inner[9] != 17:
        return None
    iihl = 4 * (inner[0] & 0x0F)
    udp = inner[iihl:]
    if len(udp) < 8 or int.from_bytes(udp[2:4], "big") != ELASTIC_PORT:
        return None
    msg = udp[8:]
    if len(msg) < 12 or msg[0] != 1:  # ACK only
        return None
    flags, atype = msg[4] >> 4, msg[4] & 0x0F
    ucount, utype = msg[5] >> 4, msg[5] & 0x0F
    if atype != 1:
        return None
    off = 8
    group = _ip(msg[off:off + 4])
    off += 4
    source = None
    if flags & 0x01:
        source = _ip(msg[off:off + 4])
        off += 4
    upstreams = []
    if utype == 1:
        for _ in range(ucount):
            if off + 4 > len(msg):
                break
            upstreams.append(_ip(msg[off:off + 4]))
            off += 4
    return {
        "outer_src": _ip(pkt[12:16]),
        "outer_dst": _ip(pkt[16:20]),
        "inner_src": _ip(inner[12:16]),
        "inner_dst": _ip(inner[16:20]),
        "group": group,
        "source": source,
        "upstreams": upstreams,
    }


def _packets_from_hex(text):
    """Split ``tcpdump -x`` output into packets (bytes, network layer up)."""
    packets = []
    current = None
    for line in text.splitlines():
        m = _HEX_LINE.match(line)
        if m:
            if current is None:
                current = bytearray()
            current.extend(bytes.fromhex(m.group(1).replace(" ", "")))
            continue
        if current is not None:
            packets.append(bytes(current))
            current = None
    if current is not None:
        packets.append(bytes(current))
    return packets


def read_ack_capture(node, tag):
    """Stop the capture on ``node`` and return the decoded EM_ACKs."""
    step(node, f"pkill -f 'tcpdump.*{_FILTER}' || true")
    text = step(node, f"sleep 1; cat em-ack-{tag}.txt || true")
    acks = []
    for pkt in _packets_from_hex(str(text)):
        ack = decode_em_ack(pkt)
        if ack is not None:
            acks.append(ack)
    return acks
