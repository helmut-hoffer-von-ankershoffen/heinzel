# Notification store compatibility fixture

These are byte-for-byte copies of `src/heinzel/kernel/_notifications.py` and
`src/heinzel/modules/system/notify/_store.py` at tag `v0.9.29`, commit
`bf2ac7fa40770f18d1aed143d030d8d61e028ecf`. The compatibility test checks their
SHA-256 digests, loads the old notice model and store in a separate interpreter,
and uses the current runtime's unchanged supporting modules. No network or Git
history is needed during the test. Keep the fixture unchanged so it tests an
actual older reader and writer, rather than a reconstruction of its schema.
