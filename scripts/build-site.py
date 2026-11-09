"""Build the Pages artifact using only explicitly public files."""
import argparse
import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path


def verify_data(source):
    pointer = json.loads((source / "latest.json").read_bytes())
    version = pointer["snapshot_id"]
    if len(version) != 64 or any(letter not in "0123456789abcdef" for letter in version):
        raise ValueError("invalid snapshot ID")
    directory = source / "snapshots" / version
    index = json.loads((directory / "index.json").read_bytes())
    ids = {stock["security_id"] for stock in index["stocks"]}
    if len(ids) != 500 or len(index["stocks"]) != 500 or version != index["snapshot_id"]:
        raise ValueError("invalid website stock universe")
    for stock in index["stocks"]:
        path = directory / "stocks" / f"{stock['security_id']}.json"
        payload = path.read_bytes()
        history = json.loads(payload)
        if sha256(payload).hexdigest() != stock["history_sha256"] or history["snapshot_id"] != version:
            raise ValueError(f"history version or hash mismatch: {path.name}")
        if (history["items"][-1] if history["items"] else None) != stock["latest"]:
            raise ValueError(f"latest history does not match home: {path.name}")
    return version, ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    if artifact.exists():
        parser.error("artifact directory must not exist")
    version, ids = verify_data(args.source)
    data = Path("public/data")
    data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source / "latest.json", data / "latest.json")
    relative = Path("snapshots") / version
    shutil.copytree(args.source / relative, data / relative, dirs_exist_ok=True)
    env = os.environ | {"NEXT_PUBLIC_PAGES": "1", "NEXT_TELEMETRY_DISABLED": "1"}
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required to build the website")
    subprocess.run([node, "node_modules/next/dist/bin/next", "build"], check=True, env=env)
    artifact.mkdir(parents=True)
    for file in ("index.html", "index.txt", "404.html"):
        shutil.copyfile(Path("out") / file, artifact / file)
    for directory in ("_next", "stocks"):
        shutil.copytree(Path("out") / directory, artifact / directory)
    (artifact / "data").mkdir()
    shutil.copyfile(data / "latest.json", artifact / "data" / "latest.json")
    shutil.copytree(data / relative, artifact / "data" / relative)
    for file in ("research.html", "research-results-4bdea2ad5b22.txt", "database.html", "database-status.json"):
        shutil.copyfile(Path("github-pages") / file, artifact / file)
    (artifact / ".nojekyll").touch()
    if not all((artifact / "stocks" / f"s-{sid}" / "index.html").is_file() for sid in ids):
        raise ValueError("missing static stock page")
    print(f"Verified {len(ids)} stock pages and their history hashes. Snapshot {version}.")


if __name__ == "__main__":
    main()
