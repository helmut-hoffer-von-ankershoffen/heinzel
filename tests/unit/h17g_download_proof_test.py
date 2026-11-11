"""APT URI records and downloaded bytes must agree exactly."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

HELPER = Path(__file__).resolve().parents[2] / "diagnostics/h17g-python/check_download.py"
spec = importlib.util.spec_from_file_location("h17g_check_download", HELPER)
assert spec
assert spec.loader
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

# Sanitized record from hosted Noble attempt 43058866718; URI and filename
# legitimately differ because APT escapes the epoch in the destination name.
NOBLE = (
    "'https://mirror.example/ubuntu/pool/main/n/nss/libnss3_3.98-1ubuntu0.3_amd64.deb' "
    "libnss3_2%3a3.98-1ubuntu0.3_amd64.deb 1444912 "
    "SHA512:9d55072ef9795fd2204f4910647c22c315fe4ffa9df298f35f44b613ceb7d9e0"
    "297729e9df2c804b65899b33724f53f65ea121fd0e2003cab45a23104e76866a\n"
)


def test_recorded_noble_record(tmp_path):
    record = tmp_path / "record"
    record.write_text(NOBLE)
    name, size, algorithm, digest = proof.parse(record, tmp_path / "apt-mirrors.txt")
    assert (name, size, algorithm) == ("libnss3_2%3a3.98-1ubuntu0.3_amd64.deb", 1444912, "SHA512")
    assert len(digest) == 128


@pytest.mark.parametrize(
    "content",
    [
        "",
        NOBLE + NOBLE,
        "warning\n" + NOBLE,
        NOBLE + "extra\n",
        NOBLE.replace("SHA512", "SHA1"),
        NOBLE.replace("SHA512", "MD5Sum"),
        NOBLE.replace("1444912", "0"),
        NOBLE.replace("1444912", "-1"),
        NOBLE.replace("SHA512:", "SHA256:"),
        NOBLE.replace("_amd64.deb", "_arm64.deb"),
        NOBLE.replace("https://", "http://"),
        NOBLE.replace("https://", "https://user@"),
        NOBLE.replace("9d5507", "zz5507"),
        NOBLE.rstrip() + " extra\n",
    ],
)
def test_reject_bad_or_ambiguous_records(tmp_path, content):
    record = tmp_path / "record"
    record.write_text(content)
    with pytest.raises(ValueError, match=r"record|URI|digest|filename|size"):
        proof.parse(record, tmp_path / "apt-mirrors.txt")


@pytest.mark.parametrize("algorithm", ["SHA256", "SHA512"])
def test_verified_bytes_and_mismatches(tmp_path, algorithm):
    data = b"representative package bytes"
    name = "libnss3_2%3a1_amd64.deb"
    package = tmp_path / name
    package.write_bytes(data)
    record = tmp_path / "record"
    digest = hashlib.new(algorithm.lower(), data).hexdigest()
    line = f"'https://mirror.example/pool/libnss3_1_amd64.deb' {name} {len(data)} {algorithm}:{digest}\n"
    record.write_text(line)
    proof.verify(record, tmp_path / "apt-mirrors.txt", package)
    package.write_bytes(data + b"x")
    with pytest.raises(ValueError, match="size"):
        proof.verify(record, tmp_path / "apt-mirrors.txt", package)
    package.write_bytes(b"x" * len(data))
    with pytest.raises(ValueError, match="digest"):
        proof.verify(record, tmp_path / "apt-mirrors.txt", package)
    package.unlink()
    package.symlink_to(record)
    with pytest.raises(OSError, match=r"."):
        proof.verify(record, tmp_path / "apt-mirrors.txt", package)


def test_reject_filename_mismatch(tmp_path):
    record = tmp_path / "record"
    record.write_text(NOBLE)
    other = tmp_path / "other.deb"
    other.write_bytes(b"x")
    with pytest.raises(ValueError, match="filename"):
        proof.verify(record, tmp_path / "apt-mirrors.txt", other)


def test_mirror_uri_is_bound_to_route_list(tmp_path):
    mirror_list = tmp_path / "apt-mirrors.txt"
    record = tmp_path / "record"
    uri = f"mirror+file:{mirror_list}/pool/main/n/nss/libnss3_1_amd64.deb"
    original = "https://mirror.example/ubuntu/pool/main/n/nss/libnss3_3.98-1ubuntu0.3_amd64.deb"
    record.write_text(NOBLE.replace(original, uri))
    assert proof.parse(record, mirror_list)[2] == "SHA512"
    with pytest.raises(ValueError, match="URI"):
        proof.parse(record, tmp_path / "other-list.txt")
    record.write_text(NOBLE.replace(original, uri.replace("/pool/", "/pool/../")))
    with pytest.raises(ValueError, match="URI"):
        proof.parse(record, mirror_list)
    record.write_text(NOBLE.replace(original, uri.replace("/pool/", "/pool/%2e%2e/")))
    with pytest.raises(ValueError, match="URI"):
        proof.parse(record, mirror_list)
