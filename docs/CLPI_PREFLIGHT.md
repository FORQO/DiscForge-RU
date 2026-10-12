# DiscForge RU — CLPI structural preflight (Full HD 2D, experimental)

The BDMV publication gate checks every numerically paired `CLIPINF/NNNNN.clpi`
with `STREAM/NNNNN.m2ts`: supported `HDMV0100`/`HDMV0200` signature,
bounded ClipInfo/SequenceInfo/ProgramInfo and optional CPI/ClipMark/Extension
section offsets, lengths, nonoverlap, and the CLPI-declared source packet
count against the paired M2TS file's byte length (`packets × 192`). Invalid
metadata blocks folder publication before exposure to the output directory.

A CLPI is read with a 16 MiB upper bound; the paired M2TS is *stat*-checked,
not read into memory. Synthetic fixtures exercise parsing and the existing
BDMV gate, but are deliberately **not playable**. Validation does not inspect
CPI entry-point seek tables, PCR/STC timing, PAT/PMT, stream PIDs and codecs,
subtitles, interactive HDMV/BD-J navigation, filesystem UDF 2.50, disc burning,
or player compatibility. Only BD25/BD50, H.264/AC-3 1080p are in scope; no
UHD, BDXL or DRM circumvention is attempted.

Run `python -m unittest discover -s tests -v` and use the existing
`python -m bdmv_preflight PATH --profile BD25` JSON output. Validate on a
real, **unencrypted** BD-RE with ASUS BW-16D1HT and a household Blu-ray
player before making any physical-compatibility claims.
