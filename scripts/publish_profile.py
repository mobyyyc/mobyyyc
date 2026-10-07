#!/usr/bin/env python3
"""Publish generated profile changes with GitHub server-side commit signing."""

import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def allowed(path):
    return (
        path == "README.md"
        or path == "data/profile.json"
        or (path.startswith("assets/") and path.endswith(".svg") and ".." not in path)
    )


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Publishing is only supported inside GitHub Actions")
    paths = git("ls-files", "--modified").splitlines()
    paths += git("ls-files", "--others", "--exclude-standard").splitlines()
    paths = sorted(set(paths))
    if not paths:
        print("Generated profile is already current; no commit needed.")
        return
    if not all(allowed(path) and (ROOT / path).is_file() for path in paths):
        raise SystemExit(
            "Refusing to publish changes outside the generated profile files"
        )
    additions = [
        {
            "path": path,
            "contents": base64.b64encode((ROOT / path).read_bytes()).decode(),
        }
        for path in paths
    ]
    payload = {
        "query": "mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid url signature{isValid}}}}",
        "variables": {
            "input": {
                "branch": {
                    "repositoryNameWithOwner": os.environ["GITHUB_REPOSITORY"],
                    "refName": os.environ["GITHUB_REF_NAME"],
                },
                "expectedHeadOid": git("rev-parse", "HEAD"),
                "message": {"headline": "chore: refresh profile artwork [skip ci]"},
                "fileChanges": {"additions": additions},
            }
        },
    }
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
            "User-Agent": "mobyyyc-profile/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        result = json.load(response)
    if result.get("errors"):
        for error in result["errors"]:
            print("::error::" + error.get("message", "Unknown GraphQL error"))
        raise SystemExit("GitHub rejected the signed update")
    commit = result["data"]["createCommitOnBranch"]["commit"]
    print(f"Updated profile: {commit['url']}")
    if not (commit.get("signature") or {}).get("isValid"):
        raise SystemExit(
            "Commit published, but GitHub did not report a valid signature"
        )


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error).get("message", error.reason)
        except (ValueError, AttributeError):
            detail = error.reason
        print(f"::error::GitHub publication failed: HTTP {error.code}: {detail}")
        raise SystemExit(1) from error
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"::error::GitHub publication connection failed: {error}")
        raise SystemExit(1) from error
