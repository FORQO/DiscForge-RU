# Experimental UDF 2.50 Metadata Partition block mapping

`tools.udf_metadata_partition.resolve_metadata_lba` resolves one bounded range
of virtual metadata blocks via the physical Metadata File's allocation table.
It requires an Extended File Entry (tag 266, ICB file type 250) with a
CRC-covered list of *recorded*, sector-aligned short allocation descriptors.
Each extent is checked against the containing physical partition and image
bounds. References cannot cross an allocation boundary in one request.

The resolver is connected to the **opt-in FSD/root-directory inspector** for
Metadata Partition Type 2 maps; the authoring/publishing gate is unchanged. Handling of
virtual/sparable maps, metadata mirrors, sparse extents, AD chains, full UDF
directory traversal, BDMV verification, and BD-J/HDMV menus remains pending.
No conclusion about hardware compatibility or Blu-ray playback is implied.

References: UDF 2.50 sections 2.2.10 (Metadata Partition Map) and 2.2.13
(Metadata Partition); ECMA TR/112 Part 3.
