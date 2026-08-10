# mGRE four-peer mutests

Hub-and-spoke underlay with GRE overlay among four peers.

```
p1 -- lan1 --\
p2 -- lan2 ---\
               r0   (static/connected routes)
p3 -- lan3 ---/
p4 -- lan4 --/
```

## Tests

| File | Example use case |
|------|------------------|
| `mutest_mgre.py` | Multipoint GRE with NBMA neighbors (unicast underlay through `r0`); peer `nrlsmf` CF with `map`/`ujoin` |
| `mutest_mgre_mcast.py` | Multicast-remote GRE (`mgre0`); `nrlsmf rmerge` on `r0` floods the underlay group; peer CF + overlay multicast |

## Run

From `tests/mutests` (requires root, FRR, `nrlsmf` on PATH):

```bash
sudo mutest mgre_four_peers
# or one file:
sudo mutest mgre_four_peers/mutest_mgre.py
sudo mutest mgre_four_peers/mutest_mgre_mcast.py
```
