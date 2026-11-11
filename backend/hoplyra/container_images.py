
from __future__ import annotations

from pathlib import Path

from hoplyra.image_catalog import bundle_path, cache_archive, cache_dir
from hoplyra.remote import RemoteRunner

IMAGES_ROOT = "/opt/hoplyra/images"


def bundle_dir(name: str) -> Path:
    return bundle_path(name)


def _remote_has_image(runner: RemoteRunner, image: str) -> bool:
    code, out, _ = runner.run(
        f"(podman image exists {image} >/dev/null 2>&1 && echo ok) || "
        f"(docker image inspect {image} >/dev/null 2>&1 && echo ok) || true"
    )
    return "ok" in out


def _load_archive_on_remote(runner: RemoteRunner, image: str, archive: Path) -> bool:
    safe = image.replace(":", "_").replace("/", "_")
    remote_tar = f"/tmp/hoplyra-image-{safe}.tar.gz"
    runner.upload_file(remote_tar, archive)
    code, out, err = runner.run(
        f"gunzip -c {remote_tar} | (podman load 2>/dev/null || docker load) && rm -f {remote_tar}",
        timeout=900,
    )
    if code != 0:
        runner.run(f"rm -f {remote_tar}", timeout=30)
        return False
    return _remote_has_image(runner, image)


def _build_on_remote(
    runner: RemoteRunner,
    image: str,
    bundle: str,
    *,
    files: list[str] | None = None,
) -> None:
    ctx = f"{IMAGES_ROOT}/{bundle}"
    runner.run(f"mkdir -p {ctx}")
    base = bundle_dir(bundle)
    names = files or sorted(p.name for p in base.iterdir() if p.is_file())
    for name in names:
        src = base / name
        mode = 0o755 if name.endswith(".sh") else 0o644
        runner.upload_text(f"{ctx}/{name}", src.read_text(), mode)

    code, _, err = runner.run(
        f"(podman build -t {image} {ctx}) || (docker build -t {image} {ctx})",
        timeout=900,
    )
    if code != 0:
        raise RuntimeError(f"image {image} build failed: {err[:500]}")


def ensure_image(
    runner: RemoteRunner,
    image: str,
    bundle: str,
    *,
    files: list[str] | None = None,
    verify_entrypoint_contains: str | None = None,
) -> None:
    if verify_entrypoint_contains and _remote_has_image(runner, image):
        code, out, _ = runner.run(
            f"podman run --rm --entrypoint grep {image} /opt/hoplyra/start.sh "
            f"-F {verify_entrypoint_contains} 2>/dev/null || true",
            timeout=90,
        )
        if verify_entrypoint_contains not in out:
            runner.run(f"podman rmi -f {image} 2>/dev/null || true")

    if _remote_has_image(runner, image):
        return

    archive = cache_archive(image)
    if archive.is_file():
        if _load_archive_on_remote(runner, image, archive):
            if verify_entrypoint_contains:
                code, out, _ = runner.run(
                    f"podman run --rm --entrypoint grep {image} /opt/hoplyra/start.sh "
                    f"-F {verify_entrypoint_contains} 2>/dev/null || true",
                    timeout=90,
                )
                if verify_entrypoint_contains not in out:
                    runner.run(f"podman rmi -f {image} 2>/dev/null || true")
                else:
                    return
            else:
                return

    _build_on_remote(runner, image, bundle, files=files)


def local_cache_ready() -> bool:
    marker = cache_dir() / ".ready"
    if not marker.is_file():
        return False
    from hoplyra.image_catalog import all_images

    return all(cache_archive(img).is_file() for img, _ in all_images())
