# Read-only M2TS audio metadata inspection (experimental)

This development utility inspects the MPEG-TS PAT and PMT in an **unencrypted**
Blu-ray-style 192-byte M2TS source. It does not burn a disc, alter video, inspect
protected discs, assert ISO/UDF validity, or establish standalone-player compatibility.

From the repository root, with a BDMV folder already authored:

```sh
python -m tools.bdmv_audio_check "Output/BDMV/STREAM/00000.m2ts" --expected-ac3 2
```

Exit code `0` means a complete CRC-valid PAT/PMT pair was found within the first
32,768 M2TS packets (6 MiB) **and** that its PMT advertised two elementary streams
with `stream_type=0x81` (Blu-ray AC-3), matching the PAT program number. It does **not** prove that audio payloads are
valid/decodable, selectable through BD-J, or in sync. Exit code `2` indicates
malformed/inconclusive metadata or a different AC-3 stream count; it does not by
itself prove the M2TS is bad. MPEG-TS sections spanning more than one 188-byte
packet are deliberately not reassembled by this bounded checker, so some valid
streams are inconclusive. Actual hardware playback and audio switching remain
mandatory before compatibility claims.

Tests: `python -m unittest tests/test_bdmv_audio_check.py -v` or normal test
discovery from the repository root.

The checker rejects malformed PSI syntax, mismatched PAT/PMT program numbers,
and missing TS sync bytes. Use this result as a diagnostic **in addition to**,
not instead of, the existing BDMV preflight and the later physical disc tests.
