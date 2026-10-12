# BDMV/BACKUP consistency preflight (Full HD BD25/BD50)

DiscForge RU checks **present** navigation-metadata backup files against
same-named primary files: `index.bdmv`, `MovieObject.bdmv`,
`PLAYLIST/NNNNN.mpls`, and `CLIPINF/NNNNN.clpi`. A present backup that differs
in size or bytes is a blocking authoring error: contradictory navigation
metadata should not be published as a ready-to-burn BDMV folder.

If the entire `BACKUP` directory or individual backup files are missing,
DiscForge emits warnings but does not fail this specific check. This avoids
conflating missing redundancy with corrupt redundancy; hardware compatibility
must still be tested. Orphaned backup MPLS/CLPI entries and malformed BACKUP directories are rejected. Symbolic links in the backup path are rejected. Reads
use fixed-size 64 KiB chunks and never inspect M2TS video through this check.

Test with `python -m unittest discover -s tests -v`. The included examples use
synthetic metadata; they are **not** playable Blu-ray images. A pass does not
validate BD-J/HDMV menus, UDF 2.50, audio/subtitle selection, or ASUS
BW-16D1HT burning/playback. UHD, 4K, and BDXL remain out of scope.
