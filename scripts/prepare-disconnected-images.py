#!/usr/bin/env python3
"""Lock rendered Kubernetes images and emit an OpenShift oc-mirror v2 config.

The input manifests must be rendered with the exact chart values intended for
deployment. Additional image references (for example, the OpenCode sandbox
image passed to ``openshell sandbox create --from``) can be supplied in a
newline-delimited file or on the command line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required; install it in the connected staging environment") from exc


IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*(?:@sha256:[0-9a-f]{64})?$")


def image_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str) and child.strip():
                yield child.strip()
            yield from image_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from image_refs(child)


def normalized_ref(ref):
    """Expand Docker Hub shorthand so the lock is unambiguous."""
    name, sep, digest = ref.partition("@")
    first = name.split("/", 1)[0]
    if "." not in first and ":" not in first and first != "localhost":
        name = "docker.io/" + ("library/" if "/" not in name else "") + name
    return name + (sep + digest if sep else "")


def resolve_digest(ref):
    if "@sha256:" in ref:
        expected = ref.rsplit("@", 1)[1]
        proc = subprocess.run(
            ["skopeo", "inspect", "--raw", "docker://" + ref],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"skopeo could not inspect {ref}: {detail}")
        actual = "sha256:" + hashlib.sha256(proc.stdout).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Digest mismatch for {ref}: registry returned {actual}")
        return expected
    proc = subprocess.run(
        ["skopeo", "inspect", "--raw", "docker://" + ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"skopeo could not inspect {ref}: {detail}")
    return "sha256:" + hashlib.sha256(proc.stdout).hexdigest()


def verify_platform(ref, platform):
    os_name, separator, architecture = platform.partition("/")
    if not separator or not os_name or not architecture:
        raise ValueError(f"Platform must be OS/architecture, got {platform!r}")
    proc = subprocess.run(
        ["skopeo", "inspect", "--override-os", os_name, "--override-arch", architecture,
         "--format", "{{.Os}}/{{.Architecture}}", "docker://" + ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode:
        detail = proc.stderr.strip()
        raise RuntimeError(f"skopeo could not inspect {ref} for {platform}: {detail}")
    actual = proc.stdout.strip()
    if actual != platform:
        raise RuntimeError(f"Image {ref} resolves to {actual}, expected {platform}")


def repository(ref):
    name = ref.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name = name.rsplit(":", 1)[0]
    return name


def load_manifests(paths):
    refs = set()
    for manifest in paths:
        with manifest.open(encoding="utf-8") as stream:
            for doc in yaml.safe_load_all(stream):
                refs.update(image_refs(doc))
    return refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", type=Path, required=True,
                        help="Rendered Kubernetes YAML; repeat for additional rendered resources")
    parser.add_argument("--images-file", type=Path,
                        help="Newline-delimited images not present in manifests")
    parser.add_argument("--image", action="append", default=[],
                        help="Additional image reference; repeat as needed")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory for images.lock.json and ImageSetConfiguration.yaml")
    parser.add_argument("--platform", default="linux/amd64",
                        help="Required target platform, in OS/architecture form (default: linux/amd64)")
    args = parser.parse_args()

    for path in args.manifest:
        if not path.is_file():
            parser.error(f"manifest does not exist: {path}")
    refs = load_manifests(args.manifest)
    refs.update(args.image)
    if args.images_file:
        if not args.images_file.is_file():
            parser.error(f"image list does not exist: {args.images_file}")
        refs.update(line.strip() for line in args.images_file.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#"))
    if not refs:
        parser.error("no image references found")

    entries = []
    for ref in sorted(refs):
        if not IMAGE_RE.fullmatch(ref) or "${" in ref or " " in ref:
            raise SystemExit(f"Invalid or unresolved image reference: {ref!r}")
        source = normalized_ref(ref)
        digest = resolve_digest(source)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SystemExit(f"Invalid sha256 digest returned for {source}: {digest}")
        verify_platform(source, args.platform)
        entries.append({"requested": ref, "source": source, "digest": digest,
                        "platform": args.platform,
                        "locked_reference": f"{source.split('@', 1)[0]}@{digest}"})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock = {
        "format": "opencode-disconnected-image-lock/v1",
        "platform": args.platform,
        "manifests": [str(path) for path in args.manifest],
        "images": entries,
    }
    (args.output_dir / "images.lock.json").write_text(
        json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    image_set = {
        "apiVersion": "mirror.openshift.io/v2alpha1",
        "kind": "ImageSetConfiguration",
        "mirror": {"additionalImages": [{"name": item["locked_reference"]} for item in entries]},
    }
    (args.output_dir / "ImageSetConfiguration.yaml").write_text(
        yaml.safe_dump(image_set, sort_keys=False), encoding="utf-8")
    chart_value_paths = {
        "ghcr.io/nvidia/openshell/gateway": "gateway.image.digest",
        "ghcr.io/nvidia/openshell/sandbox": "sandboxRuntime.image.digest",
        "ghcr.io/nvidia/openshell/supervisor": "supervisor.image.digest",
        "nvcr.io/nvidia/base/ubuntu": "sandbox.image.digest",
    }
    locked_by_repo = {repository(item["source"]): item["digest"] for item in entries}
    image_values = {"global": {"image": {"registry": "ghcr.io/nvidia", "tag": "0.1.7"}}}
    unresolved_chart_images = []
    for repo, value_path in chart_value_paths.items():
        if repo not in locked_by_repo:
            continue
        cursor = image_values
        parts = value_path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = locked_by_repo[repo]
    for item in entries:
        repo = repository(item["source"])
        if (repo.startswith("ghcr.io/nvidia/openshell/") or
                repo == "nvcr.io/nvidia/base/ubuntu") and repo not in chart_value_paths:
            unresolved_chart_images.append(item["source"])
    (args.output_dir / "openshell-image-digests.yaml").write_text(
        yaml.safe_dump(image_values, sort_keys=False), encoding="utf-8")
    print(f"Locked {len(entries)} images in {args.output_dir}")
    if unresolved_chart_images:
        print("Unmapped chart image references require a chart values override or approved tag mirror:",
              file=sys.stderr)
        for ref in unresolved_chart_images:
            print(f"  {ref}", file=sys.stderr)
    print("Review images.lock.json and openshell-image-digests.yaml before running oc mirror.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
