# Game-specific image scenarios: plan — 15 September 2026

Goal: **80–100 image scenarios for one chosen game, all different from each other.** Some are Text-to-Image and some are Image Edit. Every scenario must belong to that game. Hitting the number matters less than variety, relevance to the game, and good sources.

> **Open item:** the sheet doesn't say which game yet. This plan writes it as **[GAME]**. Nothing in steps 1–7 starts until the game is chosen.

---

## What we found in the current sheet

- The 45 gaming scenarios we have now (`image/scenarios/bank-game/`, from `gaming-character-design-scenarios-v4.xlsx`) are written for *"a modern battle-royale shooter"*, not for a real game. Most of them would fit any shooter, so **they fail the Swap Test** (step 4). We should treat them as a list of skills to test, not as final scenarios.
- The starting images for those edits (`image/assets/bank/IMG-GAME-base-*.png`) were made by `gemini-3.1-flash-image`, which is one of the models we are testing. That is unfair: one model gets to edit a picture in its own style. For the game set, **use real images from the game's official sources.**
- Keep the methods that already work: the duplicate-removal approach (`docs/2026-09-14-scenario-complexity-tiers.md`) and the small JSON file saved next to each source image (URL, sha256, size).

---

## 1. Step-by-step plan

| Step | What | Output | Owner |
|---|---|---|---|
| 1 | Pick the game and learn it well | A one-page **Game Brief** | Scenario author |
| 2 | Review every row already in the XLSX against the brief | Each row marked Keep / Replace / Remove | Reviewer |
| 3 | Write new scenarios to fill the gaps (about 130 drafts) | New rows in the Drafts tab | Scenario author |
| 4 | Find and download the original image for every edit scenario | `sources/` folder plus a Sources tab | Scenario author |
| 5 | Remove duplicates | A Removed tab with a reason for each row | Reviewer |
| 6 | Score each scenario and pick the best 80–100 | A Final tab | Reviewer |
| 7 | Final review, then hand over to Ravi | The sheet marked Ready | Senior reviewer, then Ravi |

We write about 30% more drafts than we need, so we can cut weak ones without being short.

---

## 2. Understand the game first (before writing any scenario)

Fill in a **one-page Game Brief** and put it in the first tab of the XLSX. Everyone writing or reviewing scenarios reads it first.

| Section | What to write down | Example of what's useful |
|---|---|---|
| Genre and core loop | What players do minute to minute | "Drop in, loot, rotate with the zone, final circle" |
| Mechanics | Moves, abilities, systems that show on screen | Sliding, grappling, revive, crafting, building |
| Characters | Named heroes, classes, roles, signature gear and colours | Name, silhouette, weapon, signature pose |
| Enemies and creatures | Types, bosses, pets or mounts | — |
| Environments | Named maps, biomes, landmarks, time of day and weather | Named map, its well-known buildings |
| Items | Weapons, vehicles, consumables, loot rarity colours | Rarity colour tiers |
| Visual style | Realistic, stylised, cel-shaded, pixel art. Palette, UI style | "Bright stylised, thick outlines" |
| Monetisation assets | Skins, battle pass, store cards, emotes, sprays, bundles | Store card layout, emote format |
| Marketing | Season key art, trailers, esports branding | Season names and themes |
| What does NOT belong | Things that break the game's world | "No firearms in this fantasy game" |

**Where to learn it:** the official website and press kit, official patch notes and season announcements, official trailers and dev diaries, the store pages (Steam, Play Store, App Store), the official or main community wiki, and **about 30 minutes of watching real gameplay** (or playing it). Note which source each fact came from.

---

## 3. Reference sources

### For the game (first choice)
1. **Official press or media kit.** High-resolution key art, screenshots and logos, cleared for press use. This is the best place to get original files.
2. **Official website and store pages** (Steam, Epic, Play Store, App Store). Screenshots of real gameplay and UI.
3. **Official social and YouTube channels.** Season trailers and dev diaries, good for understanding context.
4. **Official or main community wiki.** Character, map, item and skin lists with their official image files.
5. **ArtStation pages of artists credited on the game.** Concept art. Use only when the artist has posted it publicly, and record the credit.

### For real-world material we place into the game (second choice)
- **Unsplash, Pexels, Wikimedia Commons, Openverse.** Free photos of people, food, vehicles, nature and architecture, with clear licences.
- **Museum open-access collections** (The Met, Rijksmuseum). Art-style references with no copyright.

### For ideas on *what skills to test* (don't copy their prompts)
- Text-to-Image benchmarks: **PartiPrompts, DrawBench, GenEval, T2I-CompBench, GenAI-Bench**
- Image-Edit benchmarks: **MagicBrush, Emu Edit test set, PIE-Bench, GEdit-Bench, ImgEdit**

### Avoid
Pinterest, wallpaper sites, re-upload blogs, anything with a watermark, heavily compressed copies, screenshots with fan mods, and AI-generated fan art.

### Covering all 13 categories inside one game
Each requested category is turned into its game version, so we get variety **and** every scenario stays about the game:

| Category | What it means in [GAME] |
|---|---|
| People | Named heroes and operators: portraits, poses, expressions |
| Products | Weapons, gadgets, collectable items shown as product shots |
| Animals | Pets, mounts, creatures, enemies |
| Food | Healing and consumable items, in-game cafés or ration packs |
| Nature | Biomes, weather, day and night on the game's maps |
| Architecture | Named landmarks, buildings, interiors |
| Vehicles | In-game vehicles, liveries, damaged versions |
| Fashion | Skins, outfits, cosmetic sets |
| Sports | Esports and tournament graphics, stadium or arena modes |
| Gaming | Gameplay: HUD, kill-cam, lobby, loading screen, map views |
| Advertising | Season launch key art, posters, social media banners |
| E-commerce | Store bundle cards, battle pass tier art, item listings |
| Creative/artistic | Fan-event style art, chibi versions, comic panels, style changes |

*If you actually wanted a general set plus a separate game section (not everything inside one game), tell us. The steps stay the same; only the split between categories changes.*

---

## 4. How to write scenarios

### The Game Relevance tests (every scenario must pass all three)
1. **Swap Test:** replace [GAME] with a different game's name. If the scenario still makes sense without changes, it is too generic. **Reject it or rewrite it.**
2. **Named element:** the scenario uses at least one real thing from the Game Brief, such as a character, map, item, mechanic, UI element or season.
3. **Makes sense in the game:** a player or a person on the game's art team would see it and think "yes, we'd actually need that image." Nothing breaks the game's rules or style.

> ❌ Generic: "A soldier in tactical gear standing in a desert."
> ✅ Game-specific: "[Hero name] in their [Season 3 skin], doing their signature ability pose on the rooftop of [named landmark] at dusk, in the game's stylised cel-shaded look."

### Reviewing the scenarios already in the XLSX
Add these columns and fill them in for every row:

| Column | Values |
|---|---|
| Game element used | e.g. "Hero: X, Map: Y", or *none* |
| Swap Test | Pass / Fail |
| Decision | **Keep** (already specific) / **Replace** (good skill, generic content → rewrite it for the game) / **Remove** (skill doesn't fit this game) |
| Replaced by | ID of the new row |

If no good replacement exists, write a **new** scenario for that same skill using the Game Brief. Never leave a generic row in the sheet just to keep the count.

### Skills to cover (one skill per scenario, and use each skill in a *different* game situation)

**Text-to-Image**
- Build a specific in-game scene from a description (named map, time of day, action)
- Character concept that matches the official style
- Several named elements in one image (hero + vehicle + landmark)
- Exact text: season title, name plate, UI label
- A set that must match: emote sheet, rarity-tier skins
- Camera and layout: loading screen, top-down map, over-the-shoulder view
- Marketing layout: key art with space left for the logo

**Image Edit** (always starts from a real official image)
- Add or remove an object (remove the HUD from a screenshot, add a pet next to a hero)
- Swap an object (weapon A → weapon B, same grip and lighting)
- Change the background (same hero, different named map)
- Change colour or material (vehicle livery, skin colour variant)
- Edit a character, outfit or pose (new skin with the same face, victory pose)
- Change lighting or weather (map screenshot from day to night, clear to rain)
- Change the style (screenshot → chibi, pixel art or comic panel)
- Product-style edits (weapon skin on a clean store-card background)
- Change the layout (key art → square store icon, → 9:16 story banner)
- Combine several images (two heroes from separate official renders in one frame)

### Mix to aim for (about 90 final)
- **About 40 Text-to-Image and 50 Image Edit.** Edits are closer to real production work, and they're the ones that need sources.
- **Difficulty:** about 30% Low, 40% Medium, 30% High. These match our model tiers (Lite, Flash, Pro).
- **At least 4 scenarios in each of the 13 categories.** No category has more than 15% of the total.

### Each draft row needs
Short title · category · type · game element used · the full prompt or edit instruction · **what to check** (3–5 pass/fail points) · difficulty · source image (for edits).

---

## 5. Finding and downloading original images (Image Edit only)

1. **Start with official sources:** press kit, then official site or store page, then official social posts, then the wiki's file page (which usually links to the original upload).
2. **If you only have a copy, trace it back:** run a reverse image search with **Google Lens, TinEye and Bing Visual Search**. Pick the result that is **largest** and **oldest**, from the official domain.
3. **Check the file:** at least 1024 px on the short side, no watermark, no heavy compression blocks, not cropped from a bigger image (unless we cropped it on purpose, and we write that down), and no fan edits.
4. **Download the original file.** Never save a screenshot of a thumbnail.
5. **Save it with a standard name**, next to a small JSON file that works like the ones already in `image/assets/bank`:
   ```
   sources/GM-042-source.png
   sources/GM-042-source.json  →  source_url, page_url, publisher, licence/usage note,
                                  date_retrieved, width, height, sha256, found_by
   ```
6. **If you can't find the original:** set Status to **`SOURCE NOT FOUND`**, write what you tried in Notes, and add the row to the **Sources Missing** tab. That tab is sent to the project lead (you) before the final list goes out. Don't replace it with a bad copy without saying so.
7. **Usage note:** official game images are copyrighted. Use them only for internal evaluation, keep the credit, and don't publish the model outputs outside the company without checking.

---

## 6. Finding and removing duplicates

Two scenarios are **duplicates** if they test **the same skill on the same kind of subject**, even if the wording is different.

1. Give every scenario a short **fingerprint**: `Type + Skill + Subject kind`
   e.g. `Edit · background swap · single hero`. Sort the sheet by fingerprint.
2. **Same fingerprint = duplicate.** Keep the one that is harder, has clearer checks, or has a better source. Log the other one in the **Removed** tab with a reason (like the table in `2026-09-14-scenario-complexity-tiers.md`).
3. **Look out for small variations.** Changing only the colour, the hero's name, the map name or a number does **not** make a new scenario. "Recolour skin red" and "Recolour skin blue" are one scenario.
4. **Duplicate images:** two edit scenarios should not use the same source image unless they test clearly different skills. Compare image hashes. The pipeline already uses perceptual hash (pHash); treat pHash ≤ 8 as the same image.
5. **Second reviewer:** another person reads the sorted list. Anything that reads as "basically the same thing" is merged.

---

## 7. Choosing the best scenarios

Score each remaining draft. **Every "must" has to be Yes.** Then rank by total score.

| # | Check | Type |
|---|---|---|
| 1 | Passes the Game Relevance tests (Swap Test, named element, makes sense) | **Must** |
| 2 | Unique: no other scenario has its fingerprint | **Must** |
| 3 | Clear: two people would picture the same result | **Must** |
| 4 | Checkable: 3–5 pass/fail checks a reviewer can actually judge | **Must** |
| 5 | Edit only: a usable original source image is saved | **Must** (otherwise it goes to Sources Missing) |
| 6 | Tests a skill that matters for real game art or marketing work | 0–2 |
| 7 | The difficulty rating fits (not too easy for High, not impossible for Low) | 0–2 |
| 8 | Adds coverage: fills a category or skill we don't have enough of | 0–2 |
| 9 | Source quality (official and high resolution = 2, stock = 1) | 0–2 |

**Remove:** vague prompts, anything with no clear pass/fail, scenarios any model already aces (unless they're our Low tier), and anything that breaks the game's rules.

---

## 8. Putting together the final 80–100

1. Take the top-ranked scenarios and fill the mix targets: about 40 T2I and 50 Edit, 4 or more per category, and the 30/40/30 difficulty split.
2. If a category or skill is short, **write a new game-specific scenario** for it. Don't pull in a weaker duplicate.
3. Stop at 100. **If fewer than 80 pass, send what passed and tell us the gap.** Don't add filler.
4. Give final IDs `GM-001` … `GM-0NN`, and rename the source files to match.
5. **Final senior review:** go through this checklist once more:
   - [ ] Every row passes the Swap Test (read through again quickly)
   - [ ] No two rows share a fingerprint, and no source image is reused
   - [ ] All 13 categories have at least 4 rows, and T2I/Edit and difficulty are balanced
   - [ ] Every Edit row has an original file, a URL and a sha256, and the file opens
   - [ ] Every row has a prompt and checks, and a person new to the project could run it without asking questions
   - [ ] The Sources Missing tab is empty, or has been sent to the project lead

---

## 9. Handing over to Ravi

**One XLSX** (`image/scenarios/batches/game-[GAME]-v1.xlsx`) plus **one folder** (`sources/`), on the shared drive.

**Tabs:** `Game Brief` · `Final` · `Removed` (with reasons) · `Sources Missing` · `How to run`

**Columns in `Final`:**

| ID | Category | Game Element | Scenario | Type | Prompt / Edit Instruction | What to Check | Source URL | Original File | Difficulty | Notes | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GM-001 | Vehicles | [Vehicle], [Map] | Livery swap keeping decals | Image Edit | "Change the livery to…" | 1) decals kept 2) … | https://… | sources/GM-001-source.png | Medium | From press kit | Ready |

The **Game Element**, **Prompt** and **What to Check** columns are extra, beyond the columns you suggested. Without them Ravi couldn't run the scenarios or score them the same way each time.

**Status values:** `Ready` → `Running` → `Done` · or `Source missing` / `Needs rework` / `Blocked` (write the reason in Notes).

**Hand-over:** a 15-minute walkthrough with Ravi covering the Game Brief, three example rows and the status values. After that, Ravi updates Status in the sheet, and questions go in Notes rather than chat, so the answers stay with the row.
