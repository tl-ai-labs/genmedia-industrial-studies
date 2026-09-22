# Pilot analysis — 2026-09-03_225111_video

14 scenarios scored (of 14), 720p, 1 seed, blind-judged by Gemini 3 Flash (temperature 0). Tie band ±0.5.

| | Omni Flash (Gemini) | Seedance 2.5 |
|---|---|---|
| Mean score | **9.26** | **9.27** |
| Scenario wins (tie ±0.5) | 3 | 4 | (ties 7)
| Generation cost | $11.35 | $26.02 |
| Mean latency per clip | 36.7 s | 277.8 s |

## Per-criterion means

| criterion | Gemini | Seedance |
|---|---|---|
| motion_coherence | 8.71 | 8.89 |
| physics_plausibility | 9.21 | 8.86 |
| prompt_adherence | 9.61 | 9.07 |
| technical_compliance | 10.0 | 10.0 |
| temporal_consistency | 8.54 | 9.82 |
| visual_fidelity | 9.36 | 9.43 |

## Per family

| family | n | Gemini | Seedance | Gemini wins |
|---|---|---|---|---|
| cinematic-hero-shot | 4 | 9.12 | 9.89 | 0 |
| physics-action | 10 | 9.31 | 9.02 | 3 |

## Every scenario

| id | title | industry | Gemini | Seedance | Δ | winner |
|---|---|---|---|---|---|---|
| VID-CIN-01 | Slow dolly-in | Micro-drama & entertainment | 7.45 | 10.00 | -2.55 | Seedance |
| VID-CIN-02 | Golden hour consistency | Micro-drama & entertainment | 9.47 | 9.85 | -0.38 | tie |
| VID-CIN-03 | Handheld documentary | Micro-drama & entertainment | 10.00 | 9.70 | 0.3 | tie |
| VID-CIN-04 | Rack focus | Micro-drama & entertainment | 9.55 | 10.00 | -0.45 | tie |
| VID-PHY-01 | Weighted fall | Gaming | 7.75 | 9.25 | -1.5 | Seedance |
| VID-PHY-02 | Liquid pour | Gaming | 9.40 | 10.00 | -0.6 | Seedance |
| VID-PHY-03 | Cloth in wind | Gaming | 8.80 | 10.00 | -1.2 | Seedance |
| VID-PHY-04 | Collision | Gaming | 9.25 | 8.80 | 0.45 | tie |
| VID-PHY-05 | Walking gait | Gaming | 10.00 | 10.00 | 0.0 | tie |
| VID-PHY-06 | Bouncing ball | Gaming | 8.95 | 5.05 | 3.9 | Gemini |
| VID-PHY-07 | Fire and smoke | Gaming | 9.40 | 8.50 | 0.9 | Gemini |
| VID-PHY-08 | Object permanence | Gaming | 10.00 | 10.00 | 0.0 | tie |
| VID-PHY-09 | Grasp and lift | Gaming | 9.55 | 8.80 | 0.75 | Gemini |
| VID-PHY-10 | Wheel rotation | Gaming | 10.00 | 9.85 | 0.15 | tie |

## Most favourable scenarios and prompts for the Gemini arm

### 1. VID-PHY-06 — Bouncing ball  (Gemini 8.95 vs Seedance 5.05, Δ +3.90)
- **Capability under test:** Energy decay
- **Industry:** Gaming
- **Prompt (verbatim):** A rubber ball is dropped onto concrete and bounces several times, each bounce lower than the last, until it comes to rest.
- **Adherence clauses:** 1) Multiple bounces  2) Each bounce lower than the previous  3) Decay looks physically plausible  4) Ball comes to rest
- **Gemini's strongest criterion:** prompt_adherence (10.0); **Seedance's weakest:** physics_plausibility (1.0)
- **Judge on Gemini:** The video shows near-perfect visual quality and physical simulation of a bouncing ball. However, it is marred by a significant continuity error where the action restarts abruptly at the 3-second mark, suggesting a failure to generate a single continuous sequence or a poor edit of real footage.
- **Judge on Seedance:** The video fails the primary task of showing a ball bouncing on concrete. While the visual quality and consistency are excellent, the physics are completely broken as the ball oscillates in mid-air without ever touching the surface.

### 2. VID-PHY-07 — Fire and smoke  (Gemini 9.40 vs Seedance 8.50, Δ +0.90)
- **Capability under test:** Emergent media behaviour
- **Industry:** Gaming
- **Prompt (verbatim):** A candle burns on a table. The flame flickers naturally and a thin wisp of smoke rises from it. Continuous throughout.
- **Adherence clauses:** 1) Flame present and flickering  2) Smoke rises upward  3) Behaviour continuous, no popping  4) Flame stays attached to the wick
- **Gemini's strongest criterion:** prompt_adherence (10.0); **Seedance's weakest:** physics_plausibility (6.0)
- **Judge on Gemini:** A very high-quality generation that adheres strictly to the prompt. The only minor physical inaccuracy is the density of the smoke while the flame is still lit, which feels slightly stylized.
- **Judge on Seedance:** The video is visually impressive with great lighting and texture, but the smoke simulation fails significantly halfway through, producing a rigid vertical artifact that breaks the realism.

### 3. VID-PHY-09 — Grasp and lift  (Gemini 9.55 vs Seedance 8.80, Δ +0.75)
- **Capability under test:** Hand-object interaction
- **Industry:** Gaming
- **Prompt (verbatim):** A hand reaches for an apple on a table, grasps it, and lifts it. The grip must be plausible and the apple must move with the hand.
- **Adherence clauses:** 1) Reach, grasp and lift all occur in order  2) Fingers close around the object  3) Apple moves with the hand, no slipping through  4) Five fingers throughout
- **Gemini's strongest criterion:** prompt_adherence (10.0); **Seedance's weakest:** prompt_adherence (6.0)
- **Judge on Gemini:** This is an exceptionally high-quality generation that meets all the requirements of the brief with high visual realism and consistent motion.
- **Judge on Seedance:** The video is technically flawless in terms of visual quality and consistency, but it fails to complete the full sequence of actions requested by the prompt, specifically the lifting of the apple.

### 4. VID-PHY-04 — Collision  (Gemini 9.25 vs Seedance 8.80, Δ +0.45)
- **Capability under test:** Momentum transfer
- **Industry:** Gaming
- **Prompt (verbatim):** One billiard ball rolls across a table and strikes a second stationary ball. The second ball must move off in a plausible direction and the first must slow.
- **Adherence clauses:** 1) Contact occurs  2) Second ball moves only after contact  3) Directions consistent with the strike angle  4) First ball decelerates
- **Gemini's strongest criterion:** prompt_adherence (10.0); **Seedance's weakest:** visual_fidelity (7.0)
- **Judge on Gemini:** The video provides a very high-quality and physically accurate representation of the requested billiard ball collision, though it presents the action in two distinct, non-continuous shots.
- **Judge on Seedance:** The video successfully demonstrates the requested physics of momentum transfer during a collision. Its main weaknesses are the lack of true rolling motion (due to static textures) and the distorted AI-generated background elements.

### 5. VID-CIN-03 — Handheld documentary  (Gemini 10.00 vs Seedance 9.70, Δ +0.30)
- **Capability under test:** Deliberate instability without artefacts
- **Industry:** Micro-drama & entertainment
- **Prompt (verbatim):** Handheld documentary-style shot following a chef through a busy kitchen. Natural camera shake. Subject stays in frame throughout.
- **Adherence clauses:** 1) Handheld character present  2) Subject remains in frame  3) Shake reads as camera, not as artefact  4) No frame tearing or warping
- **Gemini's strongest criterion:** prompt_adherence (10.0); **Seedance's weakest:** visual_fidelity (9.0)
- **Judge on Gemini:** This video appears to be real-world footage. It perfectly adheres to the prompt and exhibits no flaws in visual quality, consistency, or physics.
- **Judge on Seedance:** This is an exceptionally high-quality generation that perfectly meets the brief. The handheld camera movement is very convincing and adds to the documentary feel without introducing any warping or digital artifacts.

### 6. VID-PHY-10 — Wheel rotation  (Gemini 10.00 vs Seedance 9.85, Δ +0.15)
- **Capability under test:** Rolling-motion consistency
- **Industry:** Gaming
- **Prompt (verbatim):** A bicycle rides past the camera from left to right. Wheel rotation speed must match the forward speed — no sliding, no reversed spin.
- **Adherence clauses:** 1) Bicycle moves left to right  2) Wheels rotate forward  3) Rotation rate consistent with translation  4) No wheel-slip appearance
- **Gemini's strongest criterion:** prompt_adherence (10.0); **Seedance's weakest:** visual_fidelity (9.5)
- **Judge on Gemini:** This video appears to be real-world footage. It meets every requirement of the prompt with perfect technical execution and no observable flaws.
- **Judge on Seedance:** This is an outstanding generation that handles the difficult task of rolling-motion consistency perfectly while maintaining high aesthetic and technical standards.

## Least favourable for Gemini (where Seedance leads)

- **VID-CIN-01 — Slow dolly-in** (Δ -2.55): Camera move accuracy and temporal stability. Judge on Gemini: The video excels at the camera movement and environmental aesthetics requested in the prompt, but fails significantly in the temporal consistency and physical realism of the character's interaction with the book.
- **VID-PHY-01 — Weighted fall** (Δ -1.50): Gravity and impact realism. Judge on Gemini: The video successfully captures the physical properties of a heavy cast-iron pan during impact, but it suffers from a lack of continuity. The jump cut between the counter and the floor leads to a change in the object's model and a break in the action, and the floor-cracking visual effect is poorly rendered.
- **VID-PHY-03 — Cloth in wind** (Δ -1.20): Soft-body dynamics. Judge on Gemini: The video is visually stunning and captures the requested aesthetic perfectly. The soft-body dynamics of the linen are very well-rendered, but the clip suffers from noticeable geometry clipping where the fabric passes through the open window frame, violating a specific requirement of the brief.
- **VID-PHY-02 — Liquid pour** (Δ -0.60): Fluid behaviour. Judge on Gemini: The video is of extremely high quality, appearing indistinguishable from a real-world recording. It perfectly captures complex fluid behaviors and refractions. The only minor issue is the final water level, which exceeds the 'two-thirds' specified in the prompt.
