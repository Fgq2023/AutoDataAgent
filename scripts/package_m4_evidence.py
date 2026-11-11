"""Bundle the cited synthetic M4 evidence with checksums for repository readers."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reject_credentials(content):
    if re.search(rb"oc_sk_[A-Za-z0-9_-]{20,}", content):
        raise ValueError("credential detected in evidence")
    if content.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                reject_credentials(archive.read(name))


def main():
    sources = [
        ROOT / "outputs/m4_live_7s5xlock/results.json",
        ROOT / "outputs/m4_live_wgp5ztqh/results.json",
        ROOT / "outputs/m4_rehearsal_jldgwo_f/results.json",
    ]
    sources += sorted((ROOT / "outputs/m4_rehearsal_jldgwo_f").rglob("*.zip"))
    if len(sources) != 9 or any(not path.is_file() for path in sources):
        raise ValueError("the cited original evidence must be present before packaging")
    destination = ROOT / "docs/evidence/m4-candidate.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            content = source.read_bytes()
            reject_credentials(content)
            name = source.relative_to(ROOT / "outputs").as_posix()
            archive.writestr(name, content)
            manifest.append({"path": name, "sha256": hashlib.sha256(content).hexdigest()})
        archive.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    print(destination, destination.stat().st_size)


if __name__ == "__main__":
    main()
