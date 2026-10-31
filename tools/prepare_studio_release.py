"""Validate both release artifacts before publishing a unified Studio release."""
import hashlib
import json
import os
import sys
from pathlib import Path


def prepare(folder, commit):
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("A full source commit SHA is required")
    assets = []
    for platform, architecture in (("Windows", "x64"), ("macOS", "arm64")):
        source = folder / f"source-{platform}.txt"
        if source.read_text().strip() != commit:
            raise ValueError(f"{platform}: source commit mismatch")
        report = json.loads((folder / f"verification-{platform}.json").read_text())
        if report.get("passed") is not True:
            raise ValueError(f"{platform}: packaged self-test did not pass")
        archive = folder / f"DiskForge-Studio-1.5.1-{platform}-{architecture}.zip"
        if archive.stat().st_size == 0:
            raise ValueError(f"{platform}: empty archive")
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        assets.append({"name": archive.name, "bytes": archive.stat().st_size, "sha256": digest})
    manifest = {"version": "1.5.1", "tag": "v1.5.1-studio", "source_commit": commit, "assets": assets}
    (folder / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (folder / "SHA256SUMS.txt").write_text("".join(f"{a['sha256']}  {a['name']}\n" for a in assets))
    for platform in ("Windows", "macOS"):
        (folder / f"source-{platform}.txt").unlink()
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), os.environ["GITHUB_SHA"])
