# Scenario complexity tiers — 14 September 2026

134 unique image scenarios, split into three tiers so each runs against the matching model tier. They come from 180 source scenarios (the existing 60, gaming character design v4, viewing angle v3), with 46 near-duplicates removed.

Full details (use case, scenario, prompt, inputs, checks, industry, evidence): `C:\Users\SangeethaS\Desktop\scenario-tiers-by-complexity.xlsx`.
Lists: `image/scenarios/tiers/{high,medium,low}.txt`, `scenario-tiers.csv`, `removed-duplicates.csv`.

| Tier | Models | Existing | Gaming | Viewing angle | Total |
|---|---|---|---|---|---|
| high | Pro / GPT-2 high | 6 | 15 | 22 | 43 |
| medium | Flash / GPT-2 medium | 16 | 21 | 18 | 55 |
| low | Lite / GPT-2 low | 23 | 9 | 4 | 36 |

The Summary sheet of the workbook explains how the tiers were decided and how duplicates were removed.

## High — Pro / GPT-2 high

| ID | Use case family | Scenario | Task | Why |
|---|---|---|---|---|
| IMG-LE-10 | Localised editing / object removal | Attribute-selective removal | image_edit | Attribute-selective removal across many small items; weakest scenario in all three pairs (Lite 4.8) |
| IMG-BRAND-04 | Product & brand imagery | Logo geometry on packaging | image_edit | Two-input logo composite at an exact size ratio; Lite arm invalid, Flash 7.2 vs Pro 9.7 |
| IMG-BRAND-05 | Product & brand imagery | Multi-SKU line-up | image_edit | Three references, equal scale, every label legible; no arm above 9.2 |
| IMG-STY-03 | Style transfer | Apply a supplied style reference | image_edit | Style from one input onto content of another; only Pro scored well (10 vs 3.0-5.7) |
| IMG-STY-05 | Style transfer | Photo to isometric render | image_edit | Photo to isometric with layout held — a geometric reprojection; Flash 3.3 |
| IMG-UI-06 | UI mockups & layout | Responsive pair | image_edit | Two reflowed layouts with nothing dropped; Flash and Lite both 6.4, Pro 10 |
| IMG-GAME-03 | Concept & ideation | Operator turnaround / model sheet | text_to_image | Four-view turnaround with identical kit and a shared horizon, from text alone |
| IMG-GAME-05 | Concept & ideation | Tactical gear callout sheet | text_to_image | Four exact labels plus leader lines that must land on the right parts |
| IMG-GAME-30 | Concept & ideation | Skin locked to a named rarity palette | text_to_image | Five-colour palette lock across subject, skin and background — zero stray hues |
| IMG-GAME-10 | Identity & consistency | Combat pose sheet from one reference | image_edit | Four combat poses of a bulky suit with extreme foreshortening, identity held |
| IMG-GAME-11 | Identity & consistency | Turnaround generated from a single front view | image_edit | Three unseen views extrapolated from one front view |
| IMG-GAME-12 | Identity & consistency | Squad lineup from three separate references | image_edit | Three references in one frame with zero feature blending |
| IMG-GAME-35 | Identity & consistency | Eight-emote sticker set | image_edit | Eight stickers, identity and outline weight held at small scale |
| IMG-GAME-38 | Identity & consistency | Two operators in a takedown interaction | image_edit | Two references in physical contact with correct scale and contact points |
| IMG-GAME-41 | Identity & consistency | Gear detail callout sheet | image_edit | Full figure plus four magnified details that must agree with it |
| IMG-GAME-42 | Identity & consistency | Weapon grip and hand study | image_edit | Three close hand-grip studies — five-digit anatomy under scrutiny |
| IMG-GAME-44 | Identity & consistency | Operator, silhouette and lobby icon in one sheet | image_edit | Silhouette must match the character at IoU >0.95, plus a matching icon |
| IMG-GAME-53 | Style & production assets | Third-person animation pose strip | image_edit | Four-frame jog cycle: real foot-contact changes, identical widths and palette |
| IMG-GAME-56 | Style & production assets | Flat-colour sheet with a palette strip | image_edit | Flat fills plus an eight-swatch strip that must match the figure's own colours |
| IMG-GAME-49 | Variants & progression | Three body types in one loadout | image_edit | Three body types, identical kit, same face on all three |
| IMG-GAME-50 | Variants & progression | Palette-swap enemy family | image_edit | Four colourways with geometry identical across panels (pHash <=8) |
| IMG-VIEW-51 | People — single and group viewpoints | Head turnaround from a single front view | image_edit | Head turnaround (three views) scored against museum photos |
| IMG-VIEW-53 | People — single and group viewpoints | Standing person seen from directly overhead | image_edit | Standing person seen from directly overhead |
| IMG-VIEW-54 | People — single and group viewpoints | One person at three camera heights | image_edit | One person at three camera heights with correct horizon shift |
| IMG-VIEW-56 | People — single and group viewpoints | Reverse angle on a group in conversation | image_edit | 180° reverse angle on a group |
| IMG-VIEW-58 | People — single and group viewpoints | View along the line of a group portrait | image_edit | View along a line of seven with correct occlusion and order |
| IMG-VIEW-59 | People — single and group viewpoints | Overhead arrangement of a group portrait | image_edit | Group of seven to overhead with exact headcount and rows |
| IMG-VIEW-38 | People, food & fashion | Raise the camera so every face is visible | image_edit | Raise camera: seven faces, one-to-one identity, nobody added |
| IMG-VIEW-24 | Product & object rotation | Side profile to a three-quarter pair shot | image_edit | Single shoe to a mirrored pair — a true mirror, not a copy |
| IMG-VIEW-27 | Product & object rotation | Reveal the port edge of a device | image_edit | 90° yaw to an unseen port edge, scored against a real photo |
| IMG-VIEW-06 | Scene & interior re-angle | Eye-level room to a high corner angle | image_edit | Room re-shot from a raised corner with every item in place |
| IMG-VIEW-07 | Scene & interior re-angle | Room photo to a top-down layout view | image_edit | Room reprojected to plan view with layout preserved |
| IMG-VIEW-08 | Scene & interior re-angle | Street-level building to a drone aerial | image_edit | Street-level to 40 m drone view — roof and surroundings invented |
| IMG-VIEW-09 | Scene & interior re-angle | Isolate one person from a group at a new angle | image_edit | Extract one person from a group and re-angle them |
| IMG-VIEW-10 | Scene & interior re-angle | Pull the camera back to a wider view | image_edit | Real dolly-back with changed perspective, not a zoom-out |
| IMG-VIEW-29 | Scene & interior re-angle | Kitchen seen from the opposite corner | image_edit | Kitchen re-shot from the opposite corner |
| IMG-VIEW-31 | Scene & interior re-angle | Front of a building to its side elevation | image_edit | Building front to its unseen side elevation |
| IMG-VIEW-33 | Scene & interior re-angle | Angled shelf to a straight-on planogram elevation | image_edit | Shelf rectification with every product order kept and labels legible |
| IMG-VIEW-16 | Technical & corrective | Flat screenshot to an angled device mockup | image_edit | Flat UI onto an angled phone with every string legible |
| IMG-VIEW-17 | Technical & corrective | Oblique photo of a document to a flat-on scan | image_edit | Oblique sign to flat scan, glare removed, text exact |
| IMG-VIEW-18 | Technical & corrective | Angled facade to a flat architectural elevation | image_edit | Perspective to true orthographic elevation |
| IMG-VIEW-44 | Technical & corrective | Angled floor plan rectified to true plan view | image_edit | Floor-plan rectification — labels exact, no line added or lost |
| IMG-VIEW-46 | Technical & corrective | Ultra-wide room to a natural focal length | image_edit | Ultra-wide to 35 mm perspective compression, same scene |

## Medium — Flash / GPT-2 medium

| ID | Use case family | Scenario | Task | Why |
|---|---|---|---|---|
| IMG-LE-02 | Localised editing / object removal | Person removal from a group | image_edit | Person removal and background reconstruction in a group; ~8.2 flat |
| IMG-LE-03 | Localised editing / object removal | Colour change on one of several identical items | image_edit | Recolour one of four identical items; ~8.7 flat |
| IMG-LE-04 | Localised editing / object removal | Remove text from a surface | image_edit | Text removal with brick-texture reconstruction; ~8.7 |
| IMG-LE-05 | Localised editing / object removal | Remove glare from glass | image_edit | Glare removal without altering artwork; GPT-low invalid, Lite 7.4 |
| IMG-LE-06 | Localised editing / object removal | Object swap preserving lighting | image_edit | Object swap keeping shadows and reflections; ~8.1 flat |
| IMG-BRAND-03 | Product & brand imagery | Brand colour tolerance | image_edit | Exact hex background; Gemini arms 8.3-8.7 in every pair |
| IMG-BRAND-06 | Product & brand imagery | Reflective surface handling | image_edit | Label legibility through reflections; Lite 8.5 |
| IMG-BRAND-08 | Product & brand imagery | Scale reference with a hand | image_edit | Hand anatomy plus product proportions; ~8 across tiers |
| IMG-STY-04 | Style transfer | Style consistency across a set | image_edit | One style across four input photos |
| IMG-STY-08 | Style transfer | Partial stylisation | image_edit | Partial stylisation with the subject untouched; Pro 6.8, Flash 8.1 |
| IMG-STY-10 | Style transfer | Style transfer with brand lock | image_edit | Style change under an exact hex lock; 9.6 flat |
| IMG-TXT-02 | Text, typography, infographics | Small-type packaging | text_to_image | Small-type exact string; Lite 7.0 |
| IMG-TXT-05 | Text, typography, infographics | Bilingual signage | text_to_image | Devanagari rendering alongside English; GPT-high 8.5 |
| IMG-TXT-08 | Text, typography, infographics | Notes to typeset diagram | image_edit | Sketch to typeset diagram, every label and edge kept; Lite 6.5 |
| IMG-TXT-10 | Text, typography, infographics | Typo correction in place | image_edit | In-place typo fix matching font; Pro 7.1 |
| IMG-UI-04 | UI mockups & layout | HTML page to mockup | image_edit | Page to image with layout and text exact; GPT-medium 8.0 |
| IMG-GAME-02 | Concept & ideation | Operator skin silhouette exploration | text_to_image | Exactly six distinct silhouettes, equal height and spacing |
| IMG-GAME-04 | Concept & ideation | Mobile low-poly operator under a triangle budget | text_to_image | Low-poly look under a hard colour/facet budget |
| IMG-GAME-07 | Concept & ideation | Uniform with a legible name tape and unit patch | text_to_image | Two short strings on curved, creased fabric |
| IMG-GAME-24 | Concept & ideation | Two-phase armoured boss | text_to_image | Two-phase boss with identity carried across the change |
| IMG-GAME-25 | Concept & ideation | Skin concept from a mood-board word list | text_to_image | Abstract word brief plus a four-colour palette constraint |
| IMG-GAME-26 | Concept & ideation | Driver designed to match a vehicle livery | text_to_image | Character and vehicle livery colour-matched |
| IMG-GAME-27 | Concept & ideation | Squad ranks across three tiers | text_to_image | Three ranks sharing one motif with escalating detail |
| IMG-GAME-29 | Concept & ideation | Infected enemy with a silhouette rule | text_to_image | Deliberately wrong anatomy under a silhouette rule |
| IMG-GAME-34 | Concept & ideation | Design brief with explicit exclusions | text_to_image | Five inclusions and five exclusions, any exclusion fails |
| IMG-GAME-09 | Identity & consistency | Lobby expression sheet from one reference | image_edit | Six-expression grid from one reference |
| IMG-GAME-13 | Identity & consistency | Same operator, fifteen years on | image_edit | Age change plus wardrobe change, identity held |
| IMG-GAME-36 | Identity & consistency | One operator, three lighting environments | image_edit | Three lighting setups with albedo held |
| IMG-GAME-37 | Identity & consistency | Three shot distances of one operator | image_edit | Three shot distances, identity held |
| IMG-GAME-39 | Identity & consistency | Four-panel kill-cam beat | image_edit | Four-panel sequence, identity and camera distance held |
| IMG-GAME-40 | Identity & consistency | Respirator off, face revealed | image_edit | Reveal an occluded face sized to the hood opening |
| IMG-GAME-22 | Style & production assets | Lobby portrait crop with a name plate | image_edit | Recompose to square crop plus exact name text, face unchanged |
| IMG-GAME-57 | Style & production assets | Battle-pass card illustration crop | image_edit | Recompose to card format with border and an empty reserved banner |
| IMG-GAME-59 | Style & production assets | Design readability at gameplay scale | image_edit | Camera moved behind and above, figure shrunk to 1/8 height |
| IMG-GAME-14 | Variants & progression | Loadout tier progression | image_edit | Three escalating tiers on one identity |
| IMG-GAME-17 | Variants & progression | Battle-damaged state | image_edit | Additive damage state, base design intact, negative constraint (no blood) |
| IMG-GAME-45 | Variants & progression | Elite variant of a base enemy | image_edit | Elite variant — amplified, not replaced |
| IMG-VIEW-22 | Camera angle from scratch | True isometric projection, no convergence | text_to_image | True isometric with 30° axes, no convergence |
| IMG-VIEW-47 | Camera angle from scratch | High-angle crowd shot | text_to_image | Crowd with one consistent elevated camera |
| IMG-VIEW-48 | Camera angle from scratch | Dutch angle at a specified tilt | text_to_image | Dutch angle at a numeric 20° |
| IMG-VIEW-49 | Camera angle from scratch | Two-point perspective street scene | text_to_image | Strict two-point perspective construction |
| IMG-VIEW-50 | Camera angle from scratch | Telephoto compression versus wide angle | text_to_image | Telephoto compression rendered convincingly |
| IMG-VIEW-52 | People — single and group viewpoints | Full-length portrait to a strict side profile | image_edit | Standing person to strict side profile |
| IMG-VIEW-60 | People — single and group viewpoints | Full-body person from an extreme low angle | image_edit | Extreme low angle with correct anatomy |
| IMG-VIEW-11 | People, food & fashion | Forty-five degree food shot to an overhead flat lay | image_edit | Food shot to overhead flat lay with arrangement held |
| IMG-VIEW-12 | People, food & fashion | Front-on garment to the back view | image_edit | Back view of a person and garment |
| IMG-VIEW-36 | People, food & fashion | Straight-on model shot to a three-quarter lifestyle angle | image_edit | Person and garment turned 35° |
| IMG-VIEW-39 | People, food & fashion | Full-body shot to a close garment detail angle | image_edit | Close camera move with real fabric detail |
| IMG-VIEW-05 | Product & object rotation | Four-step turntable sheet | image_edit | Four-angle turntable of one object |
| IMG-VIEW-23 | Product & object rotation | Show the base of the product | image_edit | Unseen base of an object, no invented markings |
| IMG-VIEW-25 | Product & object rotation | Front elevation to a three-quarter furniture angle | image_edit | Furniture to three-quarter, rear legs and pattern invented |
| IMG-VIEW-26 | Product & object rotation | Flat jewellery shot to an angled display view | image_edit | Small jewellery re-angled with fine detail |
| IMG-VIEW-14 | Technical & corrective | Correct converging verticals on a building | image_edit | Keystone correction with masonry detail held |
| IMG-VIEW-41 | Technical & corrective | Remove fisheye barrel distortion | image_edit | Fisheye to rectilinear, content retained |
| IMG-VIEW-43 | Technical & corrective | Off-axis artwork to a flat reproduction | image_edit | Artwork rectified with no reinterpretation |

## Low — Lite / GPT-2 low

| ID | Use case family | Scenario | Task | Why |
|---|---|---|---|---|
| IMG-CHAR-09 | Character & identity | Feature fidelity from reference | image_edit | New scene, features held; 10 in every arm |
| IMG-CHAR-10 | Character & identity | Persistent accessory | image_edit | Persistent accessory across scenes; Lite 9.6 |
| IMG-LE-01 | Localised editing / object removal | Single object removal from clutter | image_edit | Single object removal; 9.7 |
| IMG-LE-07 | Localised editing / object removal | Thin structure removal | image_edit | Thin power-line removal; Lite 9.7 |
| IMG-LE-08 | Localised editing / object removal | Expression change only | image_edit | Expression change only; Lite 9.6 |
| IMG-BRAND-01 | Product & brand imagery | Packshot on seamless white | image_edit | Single product on white; 10 in every arm |
| IMG-BRAND-07 | Product & brand imagery | Seasonal campaign variant | image_edit | Seasonal background swap; 10 in every arm |
| IMG-BRAND-09 | Product & brand imagery | Colourway variant | image_edit | Single colourway change; Lite 9.5 |
| IMG-STY-06 | Style transfer | Period style | image_edit | Period restyle; 10 in every arm |
| IMG-STY-07 | Style transfer | Style transfer with readable text | image_edit | Poster style keeping text; Lite 9.6 |
| IMG-STY-09 | Style transfer | Blended styles | image_edit | Blended global style; 10 in every arm |
| IMG-TXT-01 | Text, typography, infographics | Exact headline poster | text_to_image | Two exact strings on a poster; Lite 9.8 |
| IMG-TXT-03 | Text, typography, infographics | Chart from supplied values | text_to_image | Bar chart from four values; 10 in every arm |
| IMG-TXT-04 | Text, typography, infographics | Ordered process diagram | text_to_image | Five-step diagram; 10 in every arm |
| IMG-TXT-06 | Text, typography, infographics | Code rendered as an image | text_to_image | One line of code; Lite 9.1 |
| IMG-TXT-07 | Text, typography, infographics | Multi-section infographic | text_to_image | Three-section infographic; 10 in every arm |
| IMG-TXT-09 | Text, typography, infographics | Text on a curved surface | image_edit | One word on a curved surface; Lite 9.9 |
| IMG-UI-02 | UI mockups & layout | Dashboard with named widgets | text_to_image | Dashboard with four named widgets; Lite 9.7 |
| IMG-UI-03 | UI mockups & layout | Settings page with controls | text_to_image | Settings page with three toggles; Lite 9.8 |
| IMG-UI-05 | UI mockups & layout | Dark mode variant | image_edit | Dark-mode recolour; 10 in every arm |
| IMG-UI-07 | UI mockups & layout | Form with error states | text_to_image | Form with one error state; 10 |
| IMG-UI-08 | UI mockups & layout | Consistent icon set | text_to_image | Six line icons; Lite 10 |
| IMG-UI-10 | UI mockups & layout | Slide layout with placeholders | text_to_image | Slide with placeholders; Lite 10 |
| IMG-GAME-01 | Concept & ideation | Operator class concept from a design brief | text_to_image | Single-figure concept from a descriptive brief |
| IMG-GAME-31 | Concept & ideation | Companion pet design for a battle-royale lobby | text_to_image | Simple-shape pet illustration |
| IMG-GAME-33 | Concept & ideation | Stylised all-ages battle-royale character | text_to_image | Single stylised all-ages figure |
| IMG-GAME-20 | Style & production assets | Chibi version for a mobile store icon | image_edit | Chibi redraw of one figure |
| IMG-GAME-21 | Style & production assets | Clean cut-out for a game-ready asset | image_edit | Background cutout onto flat magenta (cf. BRAND-10) |
| IMG-GAME-54 | Style & production assets | Concept to a stylised 3D render look | image_edit | Global 3D-render restyle |
| IMG-GAME-55 | Style & production assets | Clean line-art pass | image_edit | Line-art conversion (cf. STY-02) |
| IMG-GAME-58 | Style & production assets | Store icon that survives downscaling | image_edit | Simplified square icon of one subject |
| IMG-GAME-16 | Variants & progression | Class re-skin, same face | image_edit | Kit swap with face locked (cf. CHAR-04) |
| IMG-VIEW-19 | Camera angle from scratch | Extreme low worm's-eye angle | text_to_image | Worm's-eye camera from text |
| IMG-VIEW-20 | Camera angle from scratch | Directly overhead flat lay | text_to_image | Overhead flat lay from text |
| IMG-VIEW-21 | Camera angle from scratch | Over-the-shoulder framing | text_to_image | Over-the-shoulder framing from text |
| IMG-VIEW-15 | Technical & corrective | Level a tilted horizon without cropping the subject | image_edit | Level a 7° tilt and fill the corners |

## Removed duplicates

| Removed | Kept instead | Reason |
|---|---|---|
| IMG-BRAND-02 | IMG-BRAND-07 | Same capability: new scene around an unchanged product |
| IMG-BRAND-10 | IMG-GAME-21 | Same capability: clean cutout, subject untouched |
| IMG-CHAR-01 | IMG-GAME-10 | Same capability: one identity across several poses |
| IMG-CHAR-02 | IMG-GAME-36 | Same capability: one identity under three lighting setups |
| IMG-CHAR-03 | IMG-GAME-13 | Same capability: identity kept through age change |
| IMG-CHAR-04 | IMG-GAME-16 | Same capability: wardrobe swap with the face locked |
| IMG-CHAR-05 | IMG-GAME-38 | Same capability: two references in one frame, no blending |
| IMG-CHAR-06 | IMG-VIEW-36 | Same capability: rotate a person about 35° to three-quarter |
| IMG-CHAR-07 | IMG-GAME-09 | Same capability: expression set on one identity |
| IMG-CHAR-08 | IMG-GAME-39 | Same capability: multi-panel story with identity held |
| IMG-GAME-06 | IMG-GAME-29 | Same capability: enemy concept from a brief (GAME-06 also has mismatched checks) |
| IMG-GAME-08 | IMG-GAME-14 | Same capability: one figure in three escalating kit states (GAME-08 also asks for a reference it has no input for) |
| IMG-GAME-15 | IMG-BRAND-09 | Same capability: recolour, geometry locked; GAME-50 covers the multi-panel version |
| IMG-GAME-18 | IMG-GAME-49 | Same capability: same kit refitted to a different body |
| IMG-GAME-19 | IMG-GAME-56 | Same capability: flat-fill stylisation of the same person |
| IMG-GAME-23 | IMG-GAME-27 | Same capability: three figures sharing one style and palette |
| IMG-GAME-28 | IMG-GAME-01 | Same capability: single-figure concept from a list of named kit |
| IMG-GAME-32 | IMG-GAME-34 | Same capability: single-figure concept with negative constraints |
| IMG-GAME-43 | IMG-GAME-16 | Same capability: civilian to tactical kit, face locked (GAME-43 title also contradicts its prompt) |
| IMG-GAME-46 | IMG-GAME-16 | Same capability: themed wardrobe swap, face locked |
| IMG-GAME-47 | IMG-GAME-17 | Same capability: damage/corruption state over an intact base design |
| IMG-GAME-48 | IMG-GAME-49 | Same capability: same kit refitted to a different body |
| IMG-GAME-51 | IMG-GAME-14 | Same capability: three-tier progression (GAME-51 also has mismatched checks) |
| IMG-GAME-52 | IMG-GAME-04 | Same capability: low-poly mobile look |
| IMG-GAME-60 | IMG-GAME-22 | Same capability: character plus exact name text, figure unchanged |
| IMG-LE-09 | IMG-LE-04 | Same capability: remove an overlay/text and restore what is behind |
| IMG-STY-01 | IMG-STY-09 | Same capability: whole-image painterly conversion; STY-09 adds a measurable blend |
| IMG-STY-02 | IMG-GAME-55 | Same capability: photo to clean line art |
| IMG-UI-01 | IMG-UI-07 | Same capability: form screen from a spec; UI-07 adds an error state |
| IMG-UI-09 | IMG-UI-10 | Same capability: layout with an empty reserved placeholder |
| IMG-VIEW-01 | IMG-VIEW-25 | Same capability: rigid object to three-quarter; settee is the harder version |
| IMG-VIEW-02 | IMG-VIEW-18 | Same capability: remove perspective to an elevation view |
| IMG-VIEW-03 | IMG-VIEW-23 | Same capability: 90° move to an unseen end of the same bottle |
| IMG-VIEW-04 | IMG-VIEW-23 | Same capability: unseen face of the same bottle |
| IMG-VIEW-13 | IMG-VIEW-60 | Same capability: lowered camera on the same kind of standing figure |
| IMG-VIEW-28 | IMG-VIEW-16 | Same capability: flat artwork onto an angled 3D mockup with text kept |
| IMG-VIEW-30 | IMG-VIEW-38 | Same capability: camera height change on a group (VIEW-30 also has mismatched checks) |
| IMG-VIEW-32 | IMG-VIEW-06 | Same capability: real camera move to a new position inside a room |
| IMG-VIEW-34 | IMG-VIEW-29 | Same capability: reverse angle across an interior |
| IMG-VIEW-35 | IMG-VIEW-11 | Same capability: tabletop food/drink to overhead (VIEW-35 also has mismatched checks) |
| IMG-VIEW-37 | IMG-VIEW-51 | Same subject and view; VIEW-51 includes the rear view plus two more |
| IMG-VIEW-40 | IMG-VIEW-11 | Same capability, reverse direction: tabletop overhead ↔ 45° |
| IMG-VIEW-42 | IMG-VIEW-17 | Same capability: planar rectification with glare removal |
| IMG-VIEW-45 | IMG-VIEW-44 | Same capability: rectify flat content keeping text and geometry |
| IMG-VIEW-55 | IMG-VIEW-12 | Same capability: rear view of a standing person |
| IMG-VIEW-57 | IMG-VIEW-59 | Same capability: group reprojected to an overhead plan |
