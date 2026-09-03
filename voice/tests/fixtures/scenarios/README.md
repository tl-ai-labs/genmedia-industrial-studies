# Sample scenarios — fixtures only, NOT part of any run

These five were written to shake out the loaders during the build. They live
here rather than in `scenarios/` so they can never enter a real run matrix:
`scenarios/` holds only the scenarios we have actually been asked to measure.

They still earn their place. Two tests in `tests/test_cost_and_config.py` read
this directory to prove that a YAML scenario and a CSV row produce the same
object, and `voi-900` is the negative control that proves the WER gate still
says no to a deliberately wrong script.

To run one of these for real, copy it into `scenarios/`.
