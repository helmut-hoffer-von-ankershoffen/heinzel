"""Synthetic installed distribution metadata for source-bound discovery tests."""

from importlib.metadata import Distribution


def installed_entry(root, entry_name, package, *, declared=False):
    info = root.parent / f"{entry_name}-1.0.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {entry_name}\nVersion: 1.0\n")
    declaration = f"[heinzel.connected_sources]\n{entry_name} = {package}\n" if declared else ""
    (info / "entry_points.txt").write_text(f"[heinzel.modules]\n{entry_name} = {package}\n" + declaration)
    paths = sorted(path.relative_to(root.parent).as_posix() for path in root.rglob("*") if path.is_file())
    (info / "RECORD").write_text("".join(f"{path},,\n" for path in paths if "__pycache__" not in path))
    return next(iter(Distribution.at(info).entry_points))
