# Full HD MPLS playlist preflight (experimental)

DiscForge RU's BDMV preflight now examines the binary structure of each
five-digit \`BDMV/PLAYLIST/*.mpls\` file, in addition to its signature/version.

The static gate checks the 40-byte header, big-endian section offsets and
lengths, non-overlapping sections, bounded PlayItem records, five-digit clip
IDs, \`M2TS\` codec identifiers, positive IN/OUT time intervals, existing
matching \`CLIPINF/*.clpi\` and \`STREAM/*.m2ts\`, and chapter-mark references.
It also checks multi-angle record bounds and warns about unsupported
multi-angle/SubPath semantics.

To avoid unbounded reads, an MPLS file larger than 16 MiB is rejected; no
video payload is loaded by this parser. Malformed or missing references
block BDMV publication through the existing preflight gate.

**What this does not prove:** a playable or standards-compliant Blu-ray,
correct CLPI contents, STN and SubPath tables, title/HDMV/BD-J navigation,
codec profile/bitstream timing, UDF 2.50 ISO layout, burning, or compatibility
with ASUS BW-16D1HT or household players. Synthetic fixtures are **not**
mountable or playable discs. Hardware validation on rewritable BD-RE remains
mandatory before any compatibility claim.

Run \`python -m unittest discover -s tests -v\` and inspect
\`bdmv_preflight.py\` JSON output for diagnostics.


### Chapter mark timing checks

For 2D Full HD playlists, entry marks (mark type 1) are chapters. Their
`mark_time_stamp` and the referenced PlayItem's `IN_time` and `OUT_time` are
all in the **same clip-local 45 kHz clock**, not a cumulative playlist clock.
Each entry mark must satisfy `IN_time <= mark_time_stamp < OUT_time`.
Timestamps before the referenced PlayItem or at/after its OUT boundary fail
BDMV preflight, as do reserved or unknown mark types. Mark type 2 (link point)
is recognized but its timing semantics are not checked; a warning is emitted.

These checks detect obviously unreachable chapter navigation targets, not
whether a chapter is on a decodable frame, if the title is reachable from a
menu, or if remote-control navigation works. The new cases are based on
synthetic MPLS records, not real authored discs.
