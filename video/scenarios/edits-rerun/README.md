# Edits re-run — the 7 scenarios without a valid pair (2026-09-11)

VID-EDIT-07 is excluded: both arms completed it on an unmodified source, so it
is already a valid pair and re-running it would pay twice for the same answer.

Everything else needs work, for one of two reasons:

- **Re-cut sources** (03, 04, 05, 08, 09) — a result already exists for
  VID-EDIT-03 on Seedance, but it was generated from the 11.24s original while
  Omni would now see the 9.58s trim. Two arms editing different clips is not a
  pair, so both arms re-run on the new source. This is why the re-run is not
  simply "the failed cells".
- **Genuinely missing** (01 Seedance, 06 both) — refused or never produced.

Expected to refuse again, at no cost:
- VID-EDIT-01 on Seedance — `InputVideoSensitiveContentDetected.PrivacyInformation`,
  source unchanged, subject faces camera.
- VID-EDIT-09 on Seedance — same shape: two subjects facing camera. If the
  trigger is a visible face rather than a person, this predicts a refusal;
  VID-EDIT-03's subject is filmed from behind and was accepted.
- VID-EDIT-06 on Omni — `recitation`, source unchanged.

Refusals bill nothing, so attempting them costs only time and settles whether
the face hypothesis holds.
