# demo/ — scenarios held back from the default run path

`scenarios/` is what `runner.cli run` picks up. Anything in here is
deliberately OUT of that path so a routine run cannot spend against it.

## voi-nar-02 — five-minute stability

5,079 characters. On ElevenLabs that is **$0.51 and 5,079 characters of quota
per clip**, so three runs need 15,237 characters. Held back so the remaining
free-tier quota stays available for the demo.

Run it explicitly when you mean to:

```bash
.venv/bin/python -m runner.cli --modality voice --scenarios demo/scenarios run --budget 2.00
```

Two of its acceptance criteria are **not implemented yet**: naturalness scored
on minute 1 and minute 5 separately, and a speaker-embedding cosine > 0.95
between them. The runner scores a clip whole and has no speaker-embedding
model. Until those land it tests the hard-pronunciation and long-form-survival
half of the brief — already far more discriminating than voi-nar-01.
