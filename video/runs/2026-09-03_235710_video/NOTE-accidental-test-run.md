# Accidental run — not part of the eval

Created 2026-09-03 23:57 local by the offline test suite, not by a human command.
`tests/test_pipeline.py::test_no_enabled_models_is_a_clean_rejection` used the
working copy's `configs/models.yaml`, in which Omni Flash was the only enabled
arm; with Application Default Credentials present the CLI proceeded past
pre-flight and generated three real clips on the three smoke scenarios.

Real spend: **$1.2163** on the Google Cloud project (Omni Flash, 3 x 4 s / 720p).
No Seedance/BytePlus spend. Kept, unedited, because runs are immutable and
spend is never hidden. Fixed the same night: the test now forces every arm
off, and an autouse fixture makes the whole suite report ADC absent and
unsets provider keys, so no test can reach a live provider again.
