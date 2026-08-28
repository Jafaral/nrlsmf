"""Example: nrlsmf command-line parsing sanity checks.

Topology (shared with other tests in this directory):

  h0 -- lan0 --\\
                 r0
  h1 -- lan1 --/

What this example covers
-------------------------
No traffic, no forwarding modes -- just a quick sanity check that
nrlsmf's command-line parser behaves the way the User's Guide says it
should: `help` prints usage and lists the documented options, `version`
(and its abbreviation `ver`) prints a version string, and unknown or
ambiguous commands fail predictably (usage text, non-zero exit) rather
than crashing or hanging. This is a fast, host-traffic-free check
that's useful to run before any of the forwarding-mode tests in this
directory, which all depend on nrlsmf's command line actually working
as documented.

See mutest_smf_merge.py, mutest_smf_cf.py, mutest_smf_elastic.py, and
mutest_smf_advertise.py for the actual forwarding-mode tests, all of
which run on this same h0/r0/h1 topology.
"""

from munet.mutest.userapi import section
from munet.mutest.userapi import step
from munet.mutest.userapi import test_step
from munet.mutest.userapi import wait_step

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
wait_step(
    "r0",
    "ip -br addr show dev eth0",
    match="10.0.0.1/24",
    desc="r0 eth0 has address 10.0.0.1/24",
)
wait_step(
    "r0",
    "ip -br addr show dev eth1",
    match="10.0.1.1/24",
    desc="r0 eth1 has address 10.0.1.1/24",
)

section("nrlsmf help")

help_output = step("r0", "sh -lc 'nrlsmf help; echo EXIT:$?'")
test_step("Usage: nrlsmf" in help_output, "nrlsmf help prints usage", target="r0")
test_step("EXIT:0" in help_output, "nrlsmf help exits successfully", target="r0")
test_step(
    "forward             {on | off}" in help_output,
    "nrlsmf help lists forward option",
    target="r0",
)
test_step(
    "relay               {on | off}" in help_output,
    "nrlsmf help lists relay option",
    target="r0",
)
test_step(
    "resequence          {on | off}" in help_output,
    "nrlsmf help lists resequence option",
    target="r0",
)
test_step(
    "window              {on | off}" in help_output,
    "nrlsmf help lists window option",
    target="r0",
)

section("nrlsmf version")

version_output = step("r0", "nrlsmf version")
test_step(
    bool(version_output.strip()),
    "nrlsmf version prints non-empty output",
    target="r0",
)

version_abbrev_output = step("r0", "sh -lc 'nrlsmf ver; echo EXIT:$?'")
test_step(
    "smf version:" in version_abbrev_output,
    "abbreviated 'ver' prints version",
    target="r0",
)
test_step("EXIT:0" in version_abbrev_output, "abbreviated 'ver' exits successfully", target="r0")

section("Unknown and ambiguous commands fail predictably")

invalid_cmd_output = step("r0", "sh -lc 'nrlsmf nope; echo EXIT:$?'")
test_step("Usage: nrlsmf" in invalid_cmd_output, "invalid command prints usage", target="r0")
test_step("EXIT:0" not in invalid_cmd_output, "invalid command exits non-zero", target="r0")

ambiguous_cmd_output = step("r0", "sh -lc 'nrlsmf r; echo EXIT:$?'")
test_step("Usage: nrlsmf" in ambiguous_cmd_output, "ambiguous command prints usage", target="r0")
test_step("EXIT:0" not in ambiguous_cmd_output, "ambiguous command exits non-zero", target="r0")

section("nrlsmf --cli local help (no running daemon)")

cli_help = step("r0", "sh -lc 'nrlsmf --cli -h; echo EXIT:$?'")
test_step("Usage: nrlsmf --cli" in cli_help, "nrlsmf --cli -h prints usage", target="r0")
test_step("EXIT:0" in cli_help, "nrlsmf --cli -h exits successfully", target="r0")

cli_show_help = step("r0", "sh -lc 'nrlsmf --cli ?; echo EXIT:$?'")
test_step("show statistics" in cli_show_help, "nrlsmf --cli ? lists show statistics", target="r0")
test_step("show tunnel" in cli_show_help, "nrlsmf --cli ? lists show tunnel", target="r0")
test_step("EXIT:0" in cli_show_help, "nrlsmf --cli ? exits successfully", target="r0")

test_step(True, "nrlsmf CLI sanity checks completed")
