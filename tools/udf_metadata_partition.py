"""Experimental UDF 2.50 Metadata File short-ad resolver.

This is deliberately read-only and not connected to the ISO publish gate.
Only a single-sector Extended File Entry with recorded, sector-aligned
short allocation descriptors is supported. It does not follow virtual,
sparable, sparse, chained, or mirror-only metadata layouts.
"""
from __future__ import annotations

from . import iso_udf_preflight as udf


def resolve_metadata_lba(handle, *, metadata_icb_lbn: int,
                         physical_start: int, physical_sectors: int,
                         metadata_lbn: int, extent_bytes: int,
                         image_sectors: int) -> int:
    """Return ISO LBA for a contiguous range of Metadata Partition blocks.

    Both metadata_icb_lbn and short_ad locations address the containing
    physical partition. metadata_lbn addresses the virtual Metadata File.
    The result is a *physical* ISO LBA, never an offset guessed from a map.
    Unsupported and malformed structures raise ValueError.
    """
    if (physical_sectors <= 0 or physical_start < 0
            or physical_start >= image_sectors
            or physical_sectors > image_sectors - physical_start):
        raise ValueError("Metadata File: invalid underlying physical partition.")
    if not 0 <= metadata_icb_lbn < physical_sectors:
        raise ValueError("Metadata File ICB is outside the physical partition.")
    if metadata_lbn < 0 or extent_bytes <= 0:
        raise ValueError("Metadata File: invalid requested logical extent.")
    required = (extent_bytes + udf.SECTOR_SIZE - 1) // udf.SECTOR_SIZE
    entry = udf._sector(handle, physical_start + metadata_icb_lbn, image_sectors)
    if udf._tag(entry, metadata_icb_lbn) != 266 or entry[27] != 250:
        raise ValueError("Metadata File ICB must be an Extended File Entry type 250.")
    if int.from_bytes(entry[34:36], "little") & 0x7:
        raise ValueError("Metadata File only supports short allocation descriptors.")
    ea_size = int.from_bytes(entry[208:212], "little")
    ad_size = int.from_bytes(entry[212:216], "little")
    ad_start = 216 + ea_size
    ad_end = ad_start + ad_size
    if ad_size == 0 or ad_size % 8 or ad_end > udf.SECTOR_SIZE:
        raise ValueError("Metadata File has invalid short-ad allocation layout.")
    crc_end = 16 + int.from_bytes(entry[10:12], "little")
    if crc_end < ad_end:
        raise ValueError("Metadata File CRC does not cover allocation descriptors.")
    information_length = int.from_bytes(entry[56:64], "little")
    if not information_length or (metadata_lbn + required) * udf.SECTOR_SIZE > information_length:
        raise ValueError("Requested metadata extent exceeds Metadata File length.")

    cursor = 0
    result = None
    for offset in range(ad_start, ad_end, 8):
        size_word = int.from_bytes(entry[offset:offset + 4], "little")
        lbn = int.from_bytes(entry[offset + 4:offset + 8], "little")
        extent_length = size_word & 0x3FFFFFFF
        if size_word >> 30 or not extent_length or extent_length % udf.SECTOR_SIZE:
            raise ValueError("Metadata File contains an unsupported short-ad extent.")
        sector_count = extent_length // udf.SECTOR_SIZE
        if lbn >= physical_sectors or sector_count > physical_sectors - lbn:
            raise ValueError("Metadata File allocation exceeds the physical partition.")
        if cursor <= metadata_lbn < cursor + sector_count:
            if required > cursor + sector_count - metadata_lbn:
                raise ValueError("Requested metadata extent crosses an allocation boundary.")
            result = physical_start + lbn + metadata_lbn - cursor
        cursor += sector_count
    if cursor * udf.SECTOR_SIZE < information_length or result is None:
        raise ValueError("Metadata File allocation is incomplete.")
    return result
