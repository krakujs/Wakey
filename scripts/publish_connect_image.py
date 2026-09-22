# SPDX-License-Identifier: Apache-2.0
"""Publish an already-built Connect image with ephemeral registry credentials."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from scripts.gcp_deploy import access_token, load_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--key-file", default=str(Path.home() / ".config/wakey/gcp-service-account.json")
    )
    args = parser.parse_args()
    account = load_key(args.key_file)
    if account["project_id"] != "wakey-ai" or not args.tag.replace("-", "").isalnum():
        parser.error("wrong credential project or invalid image tag")
    registry = "us-central1-docker.pkg.dev"
    target = f"{registry}/wakey-ai/wakey/wakey-connect:{args.tag}"
    with tempfile.TemporaryDirectory(prefix="wakey-registry-") as config:
        commands = [
            (
                [
                    "docker",
                    "--config",
                    config,
                    "login",
                    registry,
                    "-u",
                    "oauth2accesstoken",
                    "--password-stdin",
                ],
                access_token(account),
            ),
            (["docker", "tag", args.source, target], None),
            (["docker", "--config", config, "push", target], None),
        ]
        for command, stdin in commands:
            result = subprocess.run(
                command, input=stdin, text=True, capture_output=True, timeout=600, check=False
            )
            if result.returncode:
                raise SystemExit(
                    "Image publication failed; provider output suppressed to protect credentials"
                )
        inspected = subprocess.run(
            ["docker", "image", "inspect", target],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        digests = json.loads(inspected.stdout)[0]["RepoDigests"]
        digest = next(d for d in digests if d.startswith(target.rsplit(":", 1)[0] + "@sha256:"))
        print(digest)


if __name__ == "__main__":
    main()
