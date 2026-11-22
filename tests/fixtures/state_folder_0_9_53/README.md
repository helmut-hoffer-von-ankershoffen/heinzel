# State folders written by hz 0.9.53

A real tree: `generate.py.txt` (a Python script, renamed so the linters leave it alone) ran against the published 0.9.53 wheel (`uv venv --python 3.15`, `uv pip install heinzel==0.9.53`,
then `python -I generate.py OUT WORK`) and `OUT` was copied here. Nothing in it was written by the refactored code.

- `after_take/`: the state folder after `ToolSnapshot.take` on the prefix in `after_take_prefix/`.
- `interrupted/`: a put-back that stopped at its fourth rename: every entry of the tool prefix was already moved aside, so
  the prefix it left is an empty folder, and the copy and the moved-aside entries are in the state folder.

`tests/unit/runtime/updater/state_folder_golden_test.py` reads these with the current code and writes the first one
again, and asserts the bytes are the same.
