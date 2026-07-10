from hashlib import sha256
from pathlib import Path

source = Path("optimizations.patch")
destination = Path("optimizations.clean.patch")

text = source.read_text(encoding="utf-8")
text = text.replace("\r\n", "\n").replace("\r", "\n")

start_marker = "diff --git a/README.md b/README.md\n"
end_marker = '     "bytecode-depth": _fuzz_bytecode_depth,\n'

# The complete patch starts at the last README diff header.
start = text.rfind(start_marker)
if start < 0:
    raise SystemExit("Could not find the beginning of the complete patch.")

end = text.find(end_marker, start)
if end < 0:
    raise SystemExit("Could not find the end of the complete patch.")
end += len(end_marker)

patch = text[start:end].encode("utf-8")

expected_sha256 = "f0e31544bae812c894c6902a772e8661c5e091ef43fa1d98fef69f3d3a1025f8"

actual_sha256 = sha256(patch).hexdigest()
line_count = patch.count(b"\n")

if line_count != 2079:
    raise SystemExit(f"Unexpected line count: {line_count}, expected 2079.")

if actual_sha256 != expected_sha256:
    raise SystemExit(
        f"Checksum mismatch:\n"
        f"  actual:   {actual_sha256}\n"
        f"  expected: {expected_sha256}"
    )

destination.write_bytes(patch)
print(f"Wrote {destination}")
print(f"Lines:  {line_count}")
print(f"SHA256: {actual_sha256}")
