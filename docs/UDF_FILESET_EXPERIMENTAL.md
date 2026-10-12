# UDF File Set (experimental)

Run: `python -m tools.udf_fileset_preflight movie.iso --profile BD25`.

This is an opt-in, read-only test of the File Set Descriptor and root directory File Entry for direct Type 1 partitions and bounded UDF 2.50 Type 2 Metadata Partition maps. Virtual/sparable and metadata mirror mappings are not resolved. Neither directory contents nor BDMV, disc burning, or real-player playback is validated. The ISO publication gate remains unchanged. Tests use synthetic metadata fixtures rather than mountable Blu-ray images.


Root ICB hardening checks that normal File Entries (tag 261) and Extended File Entries (tag 266) have sufficient advertised extent length and descriptor CRC coverage of their fixed headers, extended-attribute sizes and allocation-descriptor sizes. Entries spanning multiple sectors are explicitly unsupported by this experimental inspector (fail closed); this is not a complete directory traversal or Blu-ray compatibility check.
