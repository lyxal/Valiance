# Round 6 runtime reproducers

30 original `.vlnc` files from the comprehensive runtime audit. Executed in
optimized/unoptimized and bytecode-serialized/unserialized modes by the
`test_all_thirty_original_round6_reproducers` test in
`tests/test_round6_runtime_regressions.py`.

Cases 01–17 and 21–26 expect controlled Valiance faults; cases 18–20 expect
exactly `1`; cases 27–30 expect promptly computed positive real square roots.
