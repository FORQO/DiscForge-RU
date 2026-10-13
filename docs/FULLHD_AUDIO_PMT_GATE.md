# Full HD AC-3 PAT/PMT publish gate (Studio, experimental)

When creating a BDMV folder or menu-enabled Windows ISO, Studio now checks
the output M2TS streams *before* publishing or creating the menu ISO.

- Source with sound: each M2TS PMT must advertise exactly one AC-3 stream.
- Silent source: each M2TS PMT must advertise zero AC-3 streams.
- Missing, encrypted, truncated or CRC-corrupt PAT/PMT fails closed.
- PSI sections may span packets. The initial scan is capped at 32,768
  M2TS packets (~6 MiB) to avoid a full BD50 read.
- On error, the temporary BDMV is discarded and no target is published.

The inspection remains opt-in when checking a foreign BDMV folder:

```sh
python bdmv_preflight.py /path/to/disc --profile BD25 --expected-ac3 1
python -m tools.bdmv_audio_check /path/to/disc/BDMV/STREAM/00000.m2ts --expected-ac3 1
```

This checks **declared PMT metadata only**. It is not codec decoding, A/V
synchronization, UDF directory-tree or player compatibility verification.
The direct menu-less tsMuxer ISO route has a UDF metadata check but does NOT
have this intermediate BDMV M2TS gate. Multi-audio support remains in
separate, stacked work such as PR #20. Tests use synthetic M2TS and mocked
encoders; real tsMuxer and ASUS BW-16D1HT hardware tests remain necessary.
