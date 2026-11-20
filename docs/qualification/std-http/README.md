# `std.http` qualification evidence

For the verified campaign report, see [validation-report.md](current-validation/validation-report.md).
For its checks and reproducible commands, see
[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) and the machine-readable
`current-validation/` directory. The older candidate-2 files are retained
for comparison and are **not** evidence that this newer source already passed.

# std.http release candidate 2 — reproducible evidence

This directory contains a **local** security and qualification campaign, not a
production release certificate. Read `release-candidate-2-report.md` first.
All JSON records were produced by the corresponding tools in this repository.
The `local-regression.json` explicitly identifies its two completed bounded
campaigns and does not double-count tests repeated after hardening.

Reproduce with `PYTHONPATH=src:tests:.` from the repository root:

```
python tools/qualify_std_http.py --timeout-seconds 165 --allow-unsupported-python --report local-regression-new.json
python tools/fuzz_std_http_protocol.py --seed 20261120 --count 1800 --report protocol-fuzz-new.json
python tools/soak_std_http.py --duration-seconds 15 --concurrency 32 --report local-soak-new.json
python tools/profile_std_http_resources.py --clients-per-batch 45 --batches 3 --report resource-profile-new.json
python tools/check_std_http_native.py --report native-dependencies-new.json
```

The override is a development-only mechanism for Python below the project's
specified minimum. Python 3.14 validation is left to the user. `std.web` can
run for more than two minutes; the runner uses a per-suite timeout and kills
remaining child processes if necessary. Sanitizer, multi-hour performance,
full protocol fuzzing and target-platform packaging signoffs remain separate.
