"""End-to-end bundle round-trip: publish → fetch → verify → apply → rollback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcli.bundle import (
    BundleFetchError,
    BundleVerifyError,
    Sha256Verifier,
    apply_bundle,
    fetch_bundle,
    load_local_manifest,
    rollback_bundle,
    verify_bundle,
)
from bcli.bundle._publish import make_bundle


def _seed_bundle_source(tmp_path: Path) -> Path:
    src = tmp_path / "bundle-finance"
    src.mkdir()
    (src / "registry.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "entity_set_name": "vendorLedgerEntries",
                        "category": "custom",
                        "publisher": "team",
                        "group": "finance",
                        "version": "v1.0",
                        "field_names": ["vendorNumber", "amount"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (src / "queries.yaml").write_text(
        "queries:\n  open-pos:\n    endpoint: purchaseOrders\n",
        encoding="utf-8",
    )
    return src


def test_make_bundle_produces_valid_manifest(tmp_path):
    src = _seed_bundle_source(tmp_path)
    out, manifest = make_bundle(
        src,
        profile="finance",
        version="2026.05.07-1",
        publisher="ops-bcli-bot",
        release_notes="initial",
        output_path=tmp_path / "finance-2026.05.07-1.tar.gz",
    )
    assert out.is_file()
    assert manifest.profile == "finance"
    assert manifest.version == "2026.05.07-1"
    assert len(manifest.checksum_sha256) == 64
    assert "registry.json" in manifest.contents
    assert "queries.yaml" in manifest.contents


def test_round_trip_apply_then_rollback(tmp_path):
    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(
        src, profile="finance", version="1.0.0", publisher="x",
        output_path=tmp_path / "finance-1.0.0.tar.gz",
    )

    bundle, raw = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    verify_bundle(bundle, verifier=Sha256Verifier(), raw_archive=raw)

    registries_dir = tmp_path / "registries"
    queries_dir = tmp_path / "queries"
    bundle_dir = tmp_path / "bundles"

    result = apply_bundle(
        bundle,
        registries_dir=registries_dir,
        queries_dir=queries_dir,
        bundle_dir=bundle_dir,
    )
    assert result.new_version == "1.0.0"
    assert result.previous_version == ""
    assert result.registry_changed is True
    assert result.queries_changed is True
    assert (registries_dir / "finance.json").is_file()
    assert (queries_dir / "finance.yaml").is_file()
    assert load_local_manifest(bundle_dir, "finance").version == "1.0.0"

    # Mutate locally to simulate user-side staleness, then apply v2.
    (registries_dir / "finance.json").write_text("{}", encoding="utf-8")
    src2 = tmp_path / "bundle-finance-v2"
    src2.mkdir()
    (src2 / "registry.json").write_text(
        json.dumps({"endpoints": [{"entity_set_name": "v2", "category": "custom", "publisher": "x", "group": "y", "version": "v1.0"}]}),
        encoding="utf-8",
    )
    archive2, _ = make_bundle(src2, profile="finance", version="2.0.0", publisher="x", output_path=tmp_path / "finance-2.0.0.tar.gz")
    bundle2, raw2 = fetch_bundle(f"file://{archive2}", allow_file_url=True)
    verify_bundle(bundle2, verifier=Sha256Verifier(), raw_archive=raw2)

    result2 = apply_bundle(
        bundle2,
        registries_dir=registries_dir,
        queries_dir=queries_dir,
        bundle_dir=bundle_dir,
    )
    assert result2.previous_version == "1.0.0"
    assert load_local_manifest(bundle_dir, "finance").version == "2.0.0"
    # The .previous backup should hold v1.0 content from before we
    # locally mutated it… but we mutated it *after* applying v1, so the
    # .previous holds the mutated stub, not the v1 bundle. That's a
    # property worth documenting: rollback restores the file that was on
    # disk just before refresh, not the file the previous refresh wrote.

    # Rollback brings back what was here at v2-apply time.
    rolled = rollback_bundle(
        "finance",
        registries_dir=registries_dir,
        queries_dir=queries_dir,
        bundle_dir=bundle_dir,
    )
    assert rolled is True
    assert (registries_dir / "finance.json").read_text(encoding="utf-8") == "{}"
    assert load_local_manifest(bundle_dir, "finance").version == "1.0.0"


def test_rollback_with_no_backup_removes_manifest(tmp_path):
    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    bundle, raw = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    verify_bundle(bundle, verifier=Sha256Verifier(), raw_archive=raw)
    apply_bundle(
        bundle,
        registries_dir=tmp_path / "registries",
        queries_dir=tmp_path / "queries",
        bundle_dir=tmp_path / "bundles",
    )
    # First rollback removes everything because there is no ".previous" — only
    # one apply happened.
    rolled = rollback_bundle(
        "finance",
        registries_dir=tmp_path / "registries",
        queries_dir=tmp_path / "queries",
        bundle_dir=tmp_path / "bundles",
    )
    assert rolled is True
    assert load_local_manifest(tmp_path / "bundles", "finance") is None


def test_tampered_file_content_rejected(tmp_path):
    """Modifying any file inside an extracted bundle must fail verification."""
    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    bundle, raw = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    # Simulate a tampered registry — could be a malicious symlink swap or
    # a CDN-side rewrite. The content hash recorded in the manifest must
    # catch it.
    (bundle.root / "registry.json").write_text("{\"endpoints\": []}", encoding="utf-8")
    with pytest.raises(BundleVerifyError, match="content mismatch"):
        verify_bundle(bundle, verifier=Sha256Verifier(), raw_archive=raw)


def test_tampered_manifest_rejected(tmp_path):
    """Swapping a contents hash in the manifest must trip the roll-up check."""
    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    bundle, raw = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    # Build a manifest where contents were swapped but the roll hash was
    # left intact — the kind of tamper a careless attacker would do.
    manifest = bundle.manifest.model_copy(
        update={"contents": {**bundle.manifest.contents, "registry.json": "0" * 64}}
    )
    bad_bundle = bundle.__class__(manifest=manifest, root=bundle.root)
    with pytest.raises(BundleVerifyError):
        verify_bundle(bad_bundle, verifier=Sha256Verifier(), raw_archive=raw)


def test_fetch_rejects_unsafe_scheme(tmp_path):
    from bcli.bundle import BundleFetchError, fetch_bundle as fetch

    with pytest.raises(BundleFetchError):
        fetch("http://example.com/bundle.tar.gz")
    with pytest.raises(BundleFetchError):
        fetch("ftp://example.com/bundle.tar.gz")


def test_fetch_rejects_file_url_without_dev_flag(tmp_path):
    """file:// is dev-only — production must refuse it without BCLI_DEV=1."""
    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    with pytest.raises(BundleFetchError, match="dev-only"):
        fetch_bundle(f"file://{archive_path}")  # no allow_file_url, no BCLI_DEV


def test_fetch_rejects_oversized_archive(tmp_path, monkeypatch):
    """Compressed-size guard: a 30MB archive must be rejected."""
    from bcli.bundle import BundleFetchError, _fetch as fetch_mod

    big = tmp_path / "big.tar.gz"
    big.write_bytes(b"\x00" * (30 * 1024 * 1024))
    monkeypatch.setattr(fetch_mod, "MAX_COMPRESSED_BYTES", 1024 * 1024)
    with pytest.raises(BundleFetchError, match="compressed-size limit"):
        fetch_bundle(f"file://{big}", allow_file_url=True)


def test_fetch_rejects_path_traversal(tmp_path):
    """A malicious bundle with `../../etc/passwd` must not extract."""
    import tarfile
    from io import BytesIO

    from bcli.bundle import BundleFetchError, fetch_bundle as fetch

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        bad = tarfile.TarInfo("../escape.txt")
        bad.size = 0
        tar.addfile(bad, BytesIO(b""))
    archive = tmp_path / "evil.tar.gz"
    archive.write_bytes(buf.getvalue())

    with pytest.raises(BundleFetchError):
        fetch(f"file://{archive}", allow_file_url=True)


def test_fetch_rejects_unexpected_filename(tmp_path):
    """Non-allowlisted filename in the tar must be rejected at extract time."""
    import tarfile
    from io import BytesIO

    from bcli.bundle import BundleFetchError, fetch_bundle as fetch

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        bad = tarfile.TarInfo("evil.sh")
        bad.size = 0
        tar.addfile(bad, BytesIO(b""))
    archive = tmp_path / "rogue.tar.gz"
    archive.write_bytes(buf.getvalue())

    with pytest.raises(BundleFetchError, match="unexpected file"):
        fetch(f"file://{archive}", allow_file_url=True)


def test_verify_rejects_traversal_in_contents_map(tmp_path):
    """Even if a manifest claims `../etc/passwd` is part of the bundle,
    the verifier must reject it before reading files."""
    from bcli.bundle import BundleVerifyError, Sha256Verifier
    from bcli.bundle._manifest import Bundle, BundleManifest
    from datetime import datetime, timezone

    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    bundle, _ = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    bad_manifest = BundleManifest(
        profile="finance",
        version="1.0.0",
        published_at=datetime.now(timezone.utc),
        checksum_sha256="0" * 64,
        contents={"../etc/passwd": "x"},
    )
    bad_bundle = Bundle(manifest=bad_manifest, root=bundle.root)
    with pytest.raises(BundleVerifyError, match="unsafe content path"):
        Sha256Verifier().verify(bad_bundle)


def test_concurrent_apply_serializes_via_lock(tmp_path):
    """Two threads applying different bundles to the same profile must not
    corrupt each other. The advisory file lock serializes them; both runs
    finish, and the on-disk manifest is one of the two versions, never a
    half-written mash."""
    import threading

    src = _seed_bundle_source(tmp_path)
    a, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    b_src = tmp_path / "bundle-finance-b"
    b_src.mkdir()
    (b_src / "registry.json").write_text(
        json.dumps({"endpoints": []}), encoding="utf-8"
    )
    b, _ = make_bundle(b_src, profile="finance", version="1.0.1", publisher="x", output_path=tmp_path / "finance-1.0.1.tar.gz")

    registries_dir = tmp_path / "registries"
    queries_dir = tmp_path / "queries"
    bundle_dir = tmp_path / "bundles"

    bundle_a, ra = fetch_bundle(f"file://{a}", allow_file_url=True)
    bundle_b, rb = fetch_bundle(f"file://{b}", allow_file_url=True)
    verify_bundle(bundle_a, verifier=Sha256Verifier(), raw_archive=ra)
    verify_bundle(bundle_b, verifier=Sha256Verifier(), raw_archive=rb)

    results: list[Exception | str] = []

    def apply(bundle):
        try:
            r = apply_bundle(
                bundle,
                registries_dir=registries_dir,
                queries_dir=queries_dir,
                bundle_dir=bundle_dir,
            )
            results.append(r.new_version)
        except Exception as e:  # noqa: BLE001
            results.append(e)

    t1 = threading.Thread(target=apply, args=(bundle_a,))
    t2 = threading.Thread(target=apply, args=(bundle_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert all(isinstance(r, str) for r in results), f"unexpected: {results}"
    assert set(results) == {"1.0.0", "1.0.1"}
    final = load_local_manifest(bundle_dir, "finance")
    assert final is not None
    assert final.version in {"1.0.0", "1.0.1"}


def test_field_lists_merge_into_registry(tmp_path):
    """Bundle's field_lists.json must be merged into registry.json on apply."""
    src = tmp_path / "bundle-fl"
    src.mkdir()
    (src / "registry.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "entity_set_name": "vendors",
                        "category": "custom",
                        "publisher": "team",
                        "group": "finance",
                        "version": "v1.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (src / "field_lists.json").write_text(
        json.dumps({"vendors": ["no", "name", "balance"]}),
        encoding="utf-8",
    )
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")
    bundle, raw = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    verify_bundle(bundle, verifier=Sha256Verifier(), raw_archive=raw)

    registries_dir = tmp_path / "registries"
    apply_bundle(
        bundle,
        registries_dir=registries_dir,
        queries_dir=tmp_path / "queries",
        bundle_dir=tmp_path / "bundles",
    )
    merged = json.loads((registries_dir / "finance.json").read_text(encoding="utf-8"))
    vendors = merged["endpoints"][0]
    assert vendors["field_names"] == ["no", "name", "balance"]


def test_no_op_refresh_does_not_change_files(tmp_path):
    """Re-applying the exact same bundle reports no changes."""
    src = _seed_bundle_source(tmp_path)
    archive_path, _ = make_bundle(src, profile="finance", version="1.0.0", publisher="x", output_path=tmp_path / "finance-1.0.0.tar.gz")

    registries_dir = tmp_path / "registries"
    queries_dir = tmp_path / "queries"
    bundle_dir = tmp_path / "bundles"

    bundle, raw = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    verify_bundle(bundle, verifier=Sha256Verifier(), raw_archive=raw)
    first = apply_bundle(
        bundle,
        registries_dir=registries_dir,
        queries_dir=queries_dir,
        bundle_dir=bundle_dir,
    )
    assert first.registry_changed is True

    # Apply the same bundle again; same content → no_change.
    bundle2, raw2 = fetch_bundle(f"file://{archive_path}", allow_file_url=True)
    verify_bundle(bundle2, verifier=Sha256Verifier(), raw_archive=raw2)
    second = apply_bundle(
        bundle2,
        registries_dir=registries_dir,
        queries_dir=queries_dir,
        bundle_dir=bundle_dir,
    )
    assert second.registry_changed is False
    assert second.queries_changed is False
