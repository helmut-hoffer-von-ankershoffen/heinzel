"""Native adapter boundaries with fake C functions; no process signaling or registration."""

import ctypes
import struct
from pathlib import Path

import pytest

from heinzel.heads import _desktop_native as native

pytestmark = pytest.mark.unit


def _adapter():
    return native.Native.__new__(native.Native)


def _kernel_bytes():
    raw = bytearray(192)
    struct.pack_into("=12I", raw, 0, 0, 0, 0, 41, 1, 501, 20, 501, 20, 501, 20, 0)
    struct.pack_into("=QQ", raw, 120, 1234, 56)
    struct.pack_into("=Q", raw, 152, 987654321)
    struct.pack_into("=i", raw, 168, 17)
    return raw


def test_combined_snapshot_uses_pid_version_not_unique_id():
    adapter = _adapter()

    def pidinfo(pid, flavor, argument, buffer, size):
        assert (pid, flavor, argument, size) == (41, 18, 0, 192)
        ctypes.memmove(buffer, bytes(_kernel_bytes()), size)
        return size

    adapter._pidinfo = pidinfo
    identity = adapter.process(41)
    assert (identity.uid, identity.real_uid, identity.saved_uid) == (501, 501, 501)
    assert identity.birth == (1234, 56)
    assert identity.unique_id == 987654321
    assert struct.unpack("=8I", identity.token) == (0, 501, 20, 501, 20, 41, 0, 17)
    calls = []
    adapter._signal = lambda token, sig: calls.append((token, sig)) or 0
    assert adapter.request_exit(identity)
    assert calls == [(identity.token, 15)]
    adapter._signal = lambda *_: 3
    assert not adapter.request_exit(identity)


@pytest.mark.parametrize("case", ["short", "different-pid", "zero-generation", "zero-birth", "zero-unique"])
def test_incomplete_or_invalid_kernel_identity_refuses(case):
    adapter = _adapter()
    raw = _kernel_bytes()
    if case == "different-pid":
        struct.pack_into("=I", raw, 12, 42)
    elif case == "zero-generation":
        struct.pack_into("=i", raw, 168, 0)
    elif case == "zero-birth":
        struct.pack_into("=Q", raw, 120, 0)
    elif case == "zero-unique":
        struct.pack_into("=Q", raw, 152, 0)

    def pidinfo(_pid, _flavor, _argument, buffer, size):
        ctypes.memmove(buffer, bytes(raw), size)
        return size - 1 if case == "short" else size

    adapter._pidinfo = pidinfo
    with pytest.raises(native.NativeUnavailableError):
        adapter.process(41)


def test_unsupported_platform_never_loads_native_libraries(monkeypatch):
    monkeypatch.setattr(native.platform, "system", lambda: "Linux")
    monkeypatch.setattr(native.c, "CDLL", lambda *_: pytest.fail("must not load"))
    with pytest.raises(native.NativeUnavailableError):
        native.Native()


def test_missing_runtime_symbol_fails_closed(monkeypatch):
    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native.platform, "mac_ver", lambda: ("15.0", (), ""))
    monkeypatch.setattr(native.c, "CDLL", lambda *_, **_options: object())
    with pytest.raises(native.NativeUnavailableError):
        native.Native()


def test_cf_ownership_releases_only_existing_objects():
    adapter = _adapter()
    released = []
    adapter._release = released.append
    with pytest.raises(native.NativeUnavailableError), adapter.owned(0):
        pytest.fail("missing pointer must refuse")
    with adapter.owned(17) as result:
        assert result == 17
    assert released == [17]


@pytest.mark.parametrize("size", [0, -1, 65537])
def test_code_identity_blob_is_bounded(size):
    adapter = _adapter()
    adapter.check_type = lambda *_: 1
    adapter._length = lambda _: size
    with pytest.raises(native.NativeUnavailableError):
        adapter.blob(1)


def test_code_identity_blob_and_string_copy_only_verified_values():
    adapter = _adapter()
    checked = []
    adapter.check_type = lambda value, kind: checked.append((value, kind))
    raw = ctypes.create_string_buffer(b"identity")
    adapter._length = lambda _: 8
    adapter._bytes = lambda _: ctypes.addressof(raw)
    assert adapter.blob(1) == b"identity"

    def text(_value, buffer, _size, encoding):
        assert encoding == 0x08000100
        buffer.value = b"team.example"
        return True

    adapter._string = text
    assert adapter.text(2) == "team.example"
    adapter._string = lambda *_: False
    with pytest.raises(native.NativeUnavailableError):
        adapter.text(2)
    assert checked == [(1, "Data"), (2, "String"), (2, "String")]


def test_registration_uses_exact_file_url_and_update_without_launch(monkeypatch):
    adapter = _adapter()
    calls = []
    adapter.core = object()
    adapter._release = lambda value: calls.append(("release", value))
    adapter._url = lambda allocator, path, length, directory: calls.append((path, length, directory)) or 77

    def function(library, name, *_types):
        assert library is adapter.core
        assert name == "LSRegisterURL"
        return lambda url, update: calls.append((url, update)) or 0

    monkeypatch.setattr(native, "_function", function)
    assert adapter.register(Path("/fictional/Heinzel.app"))
    assert calls == [(b"/fictional/Heinzel.app", 22, 1), (77, 1), ("release", 77)]


def test_adapter_resolves_exact_native_functions(monkeypatch):
    from types import SimpleNamespace

    functions = {}

    class Library:
        def __getattr__(self, name):
            result = SimpleNamespace(restype=None, argtypes=None)
            functions[name] = result
            return result

    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native.platform, "mac_ver", lambda: ("15.0", (), ""))
    monkeypatch.setattr(native.c, "CDLL", lambda _, **_options: Library())
    native.Native()
    assert functions["proc_signal_with_audittoken"].argtypes == (ctypes.c_void_p, ctypes.c_int)
    assert "kill" not in functions
    assert "proc_terminate_with_audittoken" not in functions


@pytest.mark.parametrize("status", [0, 1])
def test_copy_function_uses_unsigned_flags_and_owned_result(monkeypatch, status):
    adapter = _adapter()
    adapter.security = object()
    adapter._release = lambda _: None
    calls = []

    def function(_library, name, _result, *types):
        assert name == "SecCodeCopySigningInformation"
        assert types[-2] is ctypes.c_uint

        def run(code, flags, output):
            calls.append((code, flags))
            output._obj.value = 9
            return status

        return run

    monkeypatch.setattr(native, "_function", function)
    if status:
        with pytest.raises(native.NativeUnavailableError):
            adapter._copy("SecCodeCopySigningInformation", 7, 2)
    else:
        assert adapter._copy("SecCodeCopySigningInformation", 7, 2) == 9
    assert calls == [(7, 2)]


@pytest.mark.parametrize("kind_matches", [True, False])
def test_identity_objects_require_expected_cf_type(monkeypatch, kind_matches):
    adapter = _adapter()
    adapter.cf = object()
    adapter._type = lambda _: 9 if kind_matches else 10
    monkeypatch.setattr(native, "_function", lambda *_: lambda: 9)
    if kind_matches:
        assert adapter.check_type(1, "Data") == 1
    else:
        with pytest.raises(native.NativeUnavailableError):
            adapter.check_type(1, "Data")


@pytest.mark.parametrize("flags_valid", [True, False])
def test_signing_dictionary_extracts_exact_identity_and_releases_all_copies(flags_valid):
    adapter = _adapter()
    released = []
    adapter._release = released.append
    adapter._copy = lambda name, *_: {
        "SecCodeCopySigningInformation": 1,
        "SecCodeCopyDesignatedRequirement": 2,
        "SecRequirementCopyData": 3,
    }[name]
    adapter.key = lambda name: name
    adapter._value = lambda _info, key: {
        "kSecCodeInfoFlags": 4,
        "kSecCodeInfoTeamIdentifier": 5,
        "kSecCodeInfoIdentifier": 6,
        "kSecCodeInfoUnique": 7,
    }[key]
    adapter.check_type = lambda value, _: value

    def number(_value, kind, output):
        assert kind == 4
        output._obj.value = 0
        return flags_valid

    adapter._number = number
    adapter.text = lambda value: {5: "TEAM", 6: "team.heinzel.apple.widgets"}[value]
    adapter.blob = lambda value: {3: b"requirement", 7: b"CDHash"}[value]
    if flags_valid:
        result = adapter._identity(8)
        assert result == native.CodeIdentity(
            "team.heinzel.apple.widgets", "TEAM", adhoc=False, digest=b"CDHash", requirement=b"requirement"
        )
        assert released == [1, 3, 2]
    else:
        with pytest.raises(native.NativeUnavailableError):
            adapter._identity(8)
        assert released == [1]


@pytest.mark.parametrize("valid", [True, False])
def test_static_identity_checks_strict_seal_before_reading_code(monkeypatch, valid):
    adapter = _adapter()
    adapter.security = object()
    calls = []
    adapter._url = lambda *_: 1
    adapter._copy = lambda name, url, flags: 2
    adapter._release = lambda pointer: calls.append(("release", pointer))
    adapter._identity = lambda code: calls.append(("identity", code)) or "verified"

    def function(_library, name, *_types):
        assert name == "SecStaticCodeCheckValidity"
        return lambda code, flags, requirement: calls.append((code, flags, requirement)) or (0 if valid else 1)

    monkeypatch.setattr(native, "_function", function)
    if valid:
        assert adapter.static(Path("/fictional/code")) == "verified"
    else:
        with pytest.raises(native.NativeUnavailableError):
            adapter.static(Path("/fictional/code"))
        assert not any(call[0] == "identity" for call in calls)
    assert (2, 24, None) in calls
    assert calls[-2:] == [("release", 2), ("release", 1)]


@pytest.mark.parametrize("valid", [True, False])
def test_dynamic_identity_uses_audit_generation_and_dynamic_code(monkeypatch, valid):
    adapter = _adapter()
    adapter.cf = object()
    adapter.security = object()
    calls = []
    token = b"x" * 32
    process = native.ProcessIdentity(41, 501, 501, 501, 200, 17, (1, 2), token)
    adapter._data = lambda _allocator, value, size: calls.append((value, size)) or 1
    adapter.key = lambda name: {"kSecGuestAttributeAudit": 11, "kSecGuestAttributeDynamicCode": 12}[name]
    adapter._dictionary = lambda _allocator, keys, values, size, *_: calls.append((list(keys), list(values), size)) or 2
    adapter._copy = lambda name, host, attributes, flags: calls.append((name, host, attributes, flags)) or 3
    adapter._release = lambda pointer: calls.append(("release", pointer))
    adapter._identity = lambda code: "verified"
    monkeypatch.setattr(native, "_constant", lambda _library, name: 13 if name == "kCFBooleanTrue" else 0)
    monkeypatch.setattr(native, "_function", lambda *_: lambda *_: 0 if valid else 1)
    if valid:
        assert adapter.running(process) == "verified"
    else:
        with pytest.raises(native.NativeUnavailableError):
            adapter.running(process)
    assert calls[:3] == [(token, 32), ([11, 12], [1, 13], 2), ("SecCodeCopyGuestWithAttributes", None, 2, 0)]
    assert calls[-3:] == [("release", 3), ("release", 2), ("release", 1)]


def test_all_user_generation_does_not_request_privileged_credential_flavor():
    adapter = _adapter()

    def info(pid, flavor, argument, buffer, size):
        assert (pid, flavor, argument, size) == (41, 17, 0, 56)
        struct.pack_into("=Q", buffer, 16, 123456)
        struct.pack_into("=i", buffer, 32, 17)
        return size

    adapter._pidinfo = info
    assert adapter.generation(41) == (123456, 17)


@pytest.mark.parametrize("case", ["success", "pid-reuse", "denied", "truncated", "relative"])
def test_kernel_executable_path_is_generation_bound_and_fails_closed(case):
    adapter = _adapter()
    calls = iter([(100, 7), (101, 8) if case == "pid-reuse" else (100, 7)])
    adapter.generation = lambda _: next(calls)

    def path(token, buffer, size):
        assert struct.unpack("=8I", token) == (0, 0, 0, 0, 0, 41, 0, 7)
        assert size == 4096
        if case == "denied":
            return 0
        buffer.value = b"relative" if case == "relative" else b"/kernel/actual-executable"
        return size if case == "truncated" else len(buffer.value)

    adapter._pidpath = path
    if case == "success":
        assert adapter.executable(41) == native.ExecutableIdentity(41, Path("/kernel/actual-executable"), 100, 7)
    else:
        with pytest.raises(native.NativeUnavailableError):
            adapter.executable(41)


@pytest.mark.parametrize("case", ["zombie", "live", "unknown", "reused"])
def test_only_exact_kernel_zombie_state_can_be_excluded(case):
    adapter = _adapter()

    def info(pid, flavor, argument, buffer, size):
        assert (pid, flavor, argument, size) == (41, 13, 1, 64)
        struct.pack_into("=4I", buffer, 0, 42 if case == "reused" else pid, 1, 1, 2 if case == "live" else 5)
        return 0 if case == "unknown" else size

    adapter._pidinfo = info
    assert adapter.zombie(41) is (case == "zombie")
