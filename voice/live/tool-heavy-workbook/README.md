# Live-call scenarios — tool-heavy calls (workbook source)

150 planned phone calls, each expecting **20 or more tool calls**, for comparing
live voice agents (Gemini 3.8 Live, ElevenLabs Agents). Same method, rubrics and
input-voice recipe as the 216-call bank in `voice/live/scenario-workbook/`; ids run
from 101 (`trv-101`) so the two banks never collide.

    voice/.venv/bin/python voice/live/tool-heavy-workbook/build_workbook.py

The `.xlsx` is built output; edit `src/*.yaml` and rebuild. The build stops on any
scenario that breaks a rule, and every count in the workbook comes from the sources.

## Plan (150 calls)

| Industry | Calls | Built |
|---|---|---|
| Banking & insurance | 32 | 32 (`banking*.yaml`) |
| Ecommerce & Retail | 32 | 32 (`retail*.yaml`) |
| Travel | 32 | 32 (`travel*.yaml`) |
| Telecom | 27 | 27 (`telecom*.yaml`) |
| Healthcare | 27 | 27 (`healthcare*.yaml`) |

Languages: English 40, Hinglish 40, Hindi 26, Telugu 18, Tamil 18, Telugu ↔ English 8.
Tool calls per call: 20–24 for half, 25–29 for a third, 30+ for a sixth. About half
the calls have a tool that returns an error. Every call has a call the agent must not make.

## What differs from the 216-call builder

- **Files:** one header file per industry (`travel.yaml`: industry, brand, agent base,
  tool catalogue, groups) plus part files (`travel-1.yaml` …) with only `code` and
  `scenarios`, merged in file order.
- **Tasks:** a scenario lists `tasks: [{task, calls: [...]}]` instead of a flat
  `tools` list. The calls are flattened in order; the task names fill the new
  **Tool calls** sheet and the Scenario Bank's "Tasks in the call" column.
- **Tool errors:** a call whose fixed result starts with `error:` is an error the agent
  must recover from. The customer gives the fallback before it can happen, and the
  retry is its own expected call.
- **Build rules:** 20+ tool calls, 12–18 customer lines, at least one `forbid`, ids
  `<code>-1NN`, every `tool_order` name an expected call, and every call has `→ result`.
- **Fact checks:** every "Sat 10 Oct" weekday matches the calendar (April–December are 2026,
  January–March 2027); every `error:` result
  is retried later on the same person or item; nothing on the call day (Mon 5 Oct, or
  "today") has already landed, been debited, etc. after the 11:00 am call time.
- **Plan check:** once an industry reaches its planned count (`PLANNED`), its language
  split and tool-call tiers must match `PLAN` exactly and its tool-error count within 2.
- **Rubrics:** P1 allows 15 minutes; A6 is graded on the share of expected calls made
  exactly (95% / 85%), order rules, error recovery and no forbidden or invented calls.
- **Agent base:** one added rule — do every item, one tool call each, and say when a tool fails.
- Ads and Call the character are not in this bank.

## Writing a scenario that survives pre-recorded lines

The customer's lines are recorded once, so the customer gives a task's details
in one line: every name, the seat order, the fallback if something is taken. Each
later line has to make sense whatever order the agent worked in. `guard` says why
it holds. Totals the agent must add up go in `checks.say`, not `checks.digits`
(`digits` must appear in the facts or tool results).
