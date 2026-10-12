"""Resolve and pull immutable amd64 target manifests on the remote QA runner only."""

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def command(arguments):
    """Run a bounded trusted Docker metadata command without exposing credentials."""
    return subprocess.run(
        arguments, capture_output=True, text=True, check=True, timeout=600
    ).stdout


def main():
    """Require exact manifest bytes, platform and pulled image config identity."""
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    target = os.environ["MIRRORCODE_QA_TARGET"]
    assert target in {"cprepro", "sed"}
    repository = "ghcr.io/pablorom2004/mirrorcode"
    pins = {}
    proof = []
    import re
    from urllib.error import HTTPError
    from urllib.parse import urlencode, urlsplit
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class PublicRedirect(HTTPRedirectHandler):
        """Remove registry bearer authorization before following a cross-host blob redirect."""

        def redirect_request(self, request, fp, code, message, headers, newurl):
            """Follow only HTTPS redirects with correctly scoped transient authorization."""
            assert urlsplit(newurl).scheme == "https"
            result = super().redirect_request(
                request, fp, code, message, headers, newurl
            )
            if (
                result is not None
                and urlsplit(request.full_url).netloc != urlsplit(newurl).netloc
            ):
                result.remove_header("Authorization")
            return result

    opener = build_opener(PublicRedirect())
    token = None
    accepts = ", ".join(
        [
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ]
    )

    def fetch(kind, identifier):
        """Read exact anonymous registry bytes and obtain only a RAM-scoped public pull token."""
        nonlocal token
        url = f"https://ghcr.io/v2/pablorom2004/mirrorcode/{kind}/{identifier}"
        headers = {"Accept": accepts}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            response = opener.open(Request(url, headers=headers), timeout=90)
        except HTTPError as error:
            assert error.code == 401 and token is None
            challenge = dict(
                re.findall(r'(\w+)="([^"]+)"', error.headers["WWW-Authenticate"])
            )
            assert (
                challenge["realm"] == "https://ghcr.io/token"
                and challenge["service"] == "ghcr.io"
            )
            scope = "repository:pablorom2004/mirrorcode:pull"
            assert challenge["scope"] == scope
            token_url = (
                challenge["realm"]
                + "?"
                + urlencode({"service": challenge["service"], "scope": scope})
            )
            with opener.open(token_url, timeout=90) as token_response:
                token = json.loads(token_response.read())["token"]
            headers["Authorization"] = "Bearer " + token
            response = opener.open(Request(url, headers=headers), timeout=90)
        with response:
            return response.read(), response.headers.get("Docker-Content-Digest")

    for role in ["workspace", "agent-scoring", "reference-scoring"]:
        image_tag = f"mc_{target}_python_llm_{role}_0.1.193"
        tag = repository + ":" + image_tag
        raw, returned_digest = fetch("manifests", image_tag)
        assert returned_digest == "sha256:" + hashlib.sha256(raw).hexdigest()
        body = json.loads(raw)
        if "manifests" in body:
            matches = [
                m
                for m in body["manifests"]
                if m.get("platform", {}).get("os") == "linux"
                and m.get("platform", {}).get("architecture") == "amd64"
            ]
            assert len(matches) == 1
            digest = matches[0]["digest"]
        else:
            digest = returned_digest
        raw, returned_digest = fetch("manifests", digest)
        assert returned_digest == digest == "sha256:" + hashlib.sha256(raw).hexdigest()
        body = json.loads(raw)
        config_digest = body["config"]["digest"]
        config_raw, _ = fetch("blobs", config_digest)
        assert "sha256:" + hashlib.sha256(config_raw).hexdigest() == config_digest
        config = json.loads(config_raw)
        assert config["os"] == "linux" and config["architecture"] == "amd64"
        ref = repository + "@" + digest
        present = (
            subprocess.run(
                ["docker", "image", "inspect", ref],
                capture_output=True,
                text=True,
                timeout=15,
            ).returncode
            == 0
        )
        disk_before = shutil.disk_usage("/").free
        started = time.monotonic()
        command(["docker", "pull", "--platform", "linux/amd64", ref])
        first_pull_seconds = time.monotonic() - started
        started = time.monotonic()
        command(["docker", "pull", "--platform", "linux/amd64", ref])
        warm_pull_seconds = time.monotonic() - started
        disk_after = shutil.disk_usage("/").free
        image = json.loads(command(["docker", "image", "inspect", ref]))[0]
        assert image["Architecture"] == "amd64" and image["Os"] == "linux"
        assert ref in image["RepoDigests"] and image["Id"] == body["config"]["digest"]
        pins[tag] = ref
        proof.append(
            {
                "tag": tag,
                "manifest_digest": digest,
                "config_digest": image["Id"],
                "manifest_response_bytes_sha256_verified": True,
                "config_blob_bytes_sha256_verified": True,
                "os": "linux",
                "architecture": "amd64",
                "pulled_repo_digest_verified": True,
                "image_present_before_first_pull": present,
                "first_pull_seconds": first_pull_seconds,
                "warm_pull_seconds": warm_pull_seconds,
                "host_free_disk_before": disk_before,
                "host_free_disk_after": disk_after,
                "cache_limit": "Shared base-layer cache was not erased; first pull is not a guaranteed fully cold-layer pull",
            }
        )
    output = Path(os.environ["MIRRORCODE_QA_IMAGE_PINS"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(pins, indent=2) + "\n")
    output.with_suffix(".proof.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
