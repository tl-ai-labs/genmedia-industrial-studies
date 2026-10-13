# Image scenario bank v3 (workbook source)

300 hard image scenarios across 9 families and 6 industries, written to the bar Sahil set on
6 October 2026 (98th–99th percentile). Background, Sahil's six examples, the families and the
hardness levers: `docs/2026-10-08-v3-scenario-bank.md`.

    voice/.venv/bin/python image/v3-workbook/build_workbook.py            # validate and build
    voice/.venv/bin/python image/v3-workbook/build_workbook.py --check    # validate only

The `.xlsx` (`image/docs/sheets/2026-10-08-image-v3-scenarios.xlsx`) is built output; edit
`src/*.yaml` and rebuild. The build stops on any rule that is already broken, and every count
in the workbook comes from the sources. Nothing here runs a model.

## Progress

Complete: 300 of 300 slots, 300 scenarios (27 seeds), 78 sources, 558 outputs per model.
Every build rule passes. Written one industry at a time for the slot grid and seeds, then
family by family for the remaining scenarios.

| Industry | Slots | Seeds |
|---|---|---|
| Film, OTT & micro-drama | 75 | 7 |
| Advertising & agencies | 60 | 5 |
| E-commerce, D2C & retail | 60 | 5 |
| Gaming | 45 | 4 |
| Real estate, interiors & architecture | 30 | 3 |
| Media, publishing & events | 30 | 3 |

## Files

- `src/rubric.yaml` — the rubric both banks are scored with: criteria, what full and half marks
  look like, how checks become a score, and the pass score. The video bank reads it too.
- `src/sources.yaml` — every input image, with a model-neutral source prompt.
- `src/customers.yaml` — the target-customer types (shared with the video bank) and the 1–3
  types each scenario is for, drafted from its persona (2026-10-08, to be reviewed). Shown as the
  Scenario Bank's Target customers column and the Target customers tab.
- `src/<fam>.yaml` — one header per family (`cov`, `stb`, `sht`, `chr`, `cmp`, `typ`, `prd`,
  `edt`, `phy`): name, rubric weights and gate, failure targets and the slot grid.
- `src/<fam>-<ind>.yaml` — part files holding only `code` and `scenarios`, one per family and
  industry (`cov-flm.yaml`), merged in file order.

## Slots

The plan's tables become 300 slots before any scenario is written, so diversity is designed in.
A slot fixes everything a scenario is tagged with:

    - {id: COV-FLM-01, task: edit, subject: child-elderly, style: cinematic, light: golden,
       locale: IN, diff: extreme, target: identity-drift, sources: [SRC-KIDS-TREE],
       script: latin, idea: "..."}

`id` is family, industry and number (`COV-FLM-01`); the scenario that fills it has the same id.
`script` is `none` unless the output carries text. Allowed values for every field are at the top
of `build_workbook.py`.

## Scenarios

    - id: COV-FLM-01          # the slot it fills
      seed: true              # one of the 3 seeds per family
      sahil: angles           # recreates one of Sahil's six examples
      title, persona, evidence
      levers: [set, unseen, identity]          # 3 or more hardness levers
      inputs: [{source: SRC-KIDS-TREE, role: source, note: "..."}]
      prompt: |               # the full brief sent to the model; 80 words or more
      set_members:            # coverage and character families only
        mode: separate        # separate | chained | one-sheet
        must_stay_same: [...]
        members: [{name, prompt, checks: [[criterion, "question?"], ...]}]
      exact_text: [...]       # every string must appear word for word in the prompt
      deliverable
      checks: [[criterion, "Yes/no question?"], ...]   # 12–25 on the whole output
      competitor: "GPT Image 2 (high)"                 # the tool this is compared against
      runnable_today: {status: "yes" | partly | no, note: "..."}   # quote "yes"
      video_followon: "..."

Failure target, subject, style, light, locale, script, task and difficulty come from the slot.
A set is one scenario: the workbook counts scenarios and outputs separately.

## Rubric

Every check is a yes/no question filed under one rubric criterion (`src/rubric.yaml`): prompt
adherence, reference fidelity, preservation, set consistency, physics plausibility, text accuracy,
composition, style and visual quality. A criterion scores 10 × the share of its checks answered
yes; a scenario scores the weighted mean of the criteria it checks.

Each family header sets the weights and a gate:

    rubric:
      weights: {preservation: 4, prompt_adherence: 4, reference_fidelity: 3, ...}
      gate: [preservation]
      gate_rule: "Nothing outside the asked-for changes may be altered."

A scenario passes when every check under its gate criteria is a yes and it scores at least the
pass score (8.0). Weights are points, not shares: the share is taken over the criteria the
scenario actually checks, so a scenario with no text is not scored on text.

## Build rules

Stops with an error when any of these is broken:

- **Slots:** valid id and fields; the target is one of the family's targets; an edit slot names
  1–4 sources and a text-to-image slot names none; a text family (`SHT`, `TYP`) names a script.
- **Plan:** once an industry has slots in a family, their count and t2i/edit split match `PLAN`
  exactly, numbered 01 upward with no gaps.
- **Near-duplicates:** no two slots share family, subject, industry and failure target.
- **Running caps:** no subject above 25% of the family's planned size; extreme and hard counts
  within the family's plan (`EXTREME`, about 40% extreme).
- **Sources:** model-neutral (no model or vendor names such as Gemini, Nano Banana, Imagen, Veo,
  GPT, DALL·E, Sora, Midjourney, Recraft, Flux, Seedance, Kling, Runway; no tool syntax such as
  `--ar`, `::2`, `(word:1.3)`); 60 words or more; an acceptance checklist and an avoid list;
  `used_by` equals the slots that name the source; at most 5 uses per source.
- **Scenarios:** fill an existing slot; inputs equal the slot's sources with exactly one
  `source` role; 3 or more known levers; 12–25 checks, each a rubric criterion and a question;
  sets have a mode, 3+ must-stay-same items, the family's member count and 2–5 checks per member,
  a set consistency check and the `set` lever; prompts are model-neutral; exact text appears in
  the prompt, has a text accuracy check and uses the slot's script.
- **Rubric:** every criterion is defined with what it judges and what full and half marks look
  like; each family weights every criterion its scenarios check and names a gate; every scenario
  has a prompt adherence check and checks at least one gate criterion; reference fidelity and
  preservation are checked only where there is an input image.

Reported as **pending** until the family or the bank is complete, then enforced:

- Every family: at least 4 industries, 6 subjects, 3 styles and 4 light conditions; no subject
  above 25%; the extreme count and the t2i/edit split equal the plan; text families include
  Latin, Devanagari, Telugu and Tamil.
- Whole bank: at least 30% Indian context; t2i at least 35% and edit at least 50%; every lever
  in at least 3 families (except `set`, which only the two set families can use); every source used; Sahil's six examples present as seeds.

## Sources

Every input has a source prompt written like a photographer's brief, with the exact layout the
checks depend on, so anyone can make it with any tool. Never use a model in the comparison to
make its own inputs. Which tool made each source is recorded in a provenance file when the source
is created, not here. `alt_real` says when a real or licensed photo is acceptable instead.
