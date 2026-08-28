"""Helpers to query a running nrlsmf via ``nrlsmf --cli`` JSON show commands."""

import json

from munet.mutest.userapi import step
from munet.mutest.userapi import test_step


def cli_cmd(node, command, instance=None):
    inst = f"-i {instance} " if instance else ""
    return step(node, f"nrlsmf --cli {inst}-c {json.dumps(command)}")


def parse_json_blob(text):
    text = text.strip()
    start = None
    for i, ch in enumerate(text):
        if ch in "[{":
            start = i
            break
    if start is None:
        raise ValueError(f"no JSON object in {text[:200]!r}")
    text = text[start:]
    end_obj = text.rfind("}")
    end_arr = text.rfind("]")
    end = max(end_obj, end_arr)
    if end >= 0:
        text = text[: end + 1]
    return json.loads(text)


def show_json(node, show_cmd, instance=None):
    raw = cli_cmd(node, f"{show_cmd} json", instance)
    try:
        data = parse_json_blob(raw)
    except (json.JSONDecodeError, ValueError) as err:
        test_step(
            False,
            f"{node} '{show_cmd} json' parse failed: {err}: {raw[:240]!r}",
            target=node,
        )
        return None
    return data


def check_common_show(node, instance=None, group_name=None, ifaces=None):
    """Ping plus JSON show version/statistics/interface/grouping."""
    pong = cli_cmd(node, "ping", instance)
    test_step("pong" in pong, f"{node} --cli ping returns pong", target=node)

    ver = show_json(node, "show version", instance)
    if ver is not None:
        test_step(
            isinstance(ver, dict) and bool(ver.get("jsonVersion")),
            f"{node} show version json has jsonVersion",
            target=node,
        )

    stats = show_json(node, "show statistics", instance)
    if stats is not None:
        test_step(isinstance(stats, list) and len(stats) >= 1,
                  f"{node} show statistics json is a non-empty list", target=node)
        names = {row.get("interface") for row in stats if isinstance(row, dict)}
        for iface in ifaces or ():
            test_step(iface in names, f"{node} statistics includes {iface}", target=node)

    listing = show_json(node, "show interface", instance)
    if listing is not None:
        test_step(isinstance(listing, list) and len(listing) >= 1,
                  f"{node} show interface json is a non-empty list", target=node)
        names = {row.get("Interface") for row in listing if isinstance(row, dict)}
        for iface in ifaces or ():
            test_step(iface in names, f"{node} interface list includes {iface}", target=node)

    grouping = show_json(node, "show interface grouping", instance)
    if grouping is not None:
        test_step(isinstance(grouping, list) and len(grouping) >= 1,
                  f"{node} show interface grouping json is a non-empty list", target=node)
        if group_name:
            gnames = {g.get("GroupName") for g in grouping if isinstance(g, dict)}
            test_step(group_name in gnames,
                      f"{node} grouping includes {group_name}", target=node)


def check_show_groups(node, instance=None, mcast_addr=None):
    groups = show_json(node, "show groups", instance)
    if groups is None:
        return
    test_step(isinstance(groups, list), f"{node} show groups json is a list", target=node)
    if mcast_addr:
        addrs = {g.get("MCastAddr") for g in groups if isinstance(g, dict)}
        test_step(mcast_addr in addrs,
                  f"{node} show groups includes {mcast_addr}", target=node)


def _rows_for_iface(rows, iface):
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("Interface") == iface]


def check_show_tunnel(node, instance, iface, local=None, remotes=None, overlay_ip=None,
                      want_c=None):
    """Assert show tunnel json rows for iface (underlay Local/Remote, overlay IP)."""
    rows = show_json(node, "show tunnel", instance)
    if rows is None:
        return
    on_if = _rows_for_iface(rows, iface)
    test_step(bool(on_if), f"{node} show tunnel has {iface}", target=node)
    if local:
        test_step(any(r.get("Local") == local for r in on_if),
                  f"{node} show tunnel Local {local} on {iface}", target=node)
    if overlay_ip:
        test_step(any(r.get("IP") == overlay_ip for r in on_if),
                  f"{node} show tunnel IP {overlay_ip} on {iface}", target=node)
    if remotes:
        have = {r.get("Remote") for r in on_if}
        for remote in remotes:
            test_step(remote in have,
                      f"{node} show tunnel Remote {remote} on {iface}", target=node)
    if want_c is True:
        test_step(any("C" in (r.get("Flags") or "") for r in on_if),
                  f"{node} show tunnel Flags include C on {iface}", target=node)
    elif want_c is False:
        test_step(all("C" not in (r.get("Flags") or "") for r in on_if),
                  f"{node} show tunnel Flags omit C on {iface}", target=node)


def check_show_neighbors(node, instance, iface, remotes=None, neighbor_ips=None,
                         min_count=0, want_c=None):
    """Assert show tunnel neighbors json for iface (Neighbor IP / Remote)."""
    rows = show_json(node, "show tunnel neighbors", instance)
    if rows is None:
        return
    on_if = _rows_for_iface(rows, iface)
    test_step(len(on_if) >= min_count,
              f"{node} show tunnel neighbors has >= {min_count} row(s) on {iface}",
              target=node)
    if remotes:
        have = {r.get("Remote") for r in on_if}
        for remote in remotes:
            test_step(remote in have,
                      f"{node} neighbor Remote {remote} on {iface}", target=node)
    if neighbor_ips:
        have = {r.get("NeighborIP") for r in on_if}
        for nip in neighbor_ips:
            test_step(nip in have,
                      f"{node} Neighbor IP {nip} on {iface}", target=node)
    if want_c is True:
        test_step(any("C" in (r.get("Flags") or "") for r in on_if),
                  f"{node} neighbor Flags include C on {iface}", target=node)
