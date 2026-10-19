# How to design a Zook

You are designing a creature (a "Zook") for BAMZOOKi, a 2006 game where creatures built in the
Zook Kit compete in physics contests. Your design is loaded into the original game engine
(Bonsai, with MathEngine Karma rigid-body physics). Everything here comes from the game's own
code and from tests in this harness. Points marked *(inferred)* are reasoned from the code but
not separately measured.

You design the **body** and its **gait settings**. You don't write behaviour. The engine builds
each Zook's controller from the body: which parts move, their stride paths and phases, how they
steer. Contests then steer every Zook towards targets (the opponent, a ball, a finish line).

## 1. Units, axes, weight

- 1 unit = 4 cm. Passport stats (Height/Length/Width) are bounding-box size × 4 cm.
- Axes: **y is up**. The Zook **faces +z**: its front is the root part's +z end. x is sideways.
- **Weight** = sum over all parts of (4·width)·(4·height)·(4·length) grams. That is density 1
  applied to each part's bounding box, so a size-1 cube-ish part weighs 64 g and a 3×3×3 part
  weighs 1.7 kg. Big parts get heavy fast.
- At the start the Zook is set down with its lowest point on the floor at its start position.
- Reference, the example Zooks (stats as the Zook Kit records them):

| Zook | Height × Length × Width (cm) | Weight | Parts | Sprint | Block push | Movement |
|---|---|---|---|---|---|---|
| Spider | 4.7 × 16.8 × 21.8 | 0.49 kg | 19 | 54.9 cm/s | 14.2 cm | 8 two-part (double) legs |
| Leapsa | 7.2 × 22.2 × 30.9 | 0.43 kg | 18 | 32.0 cm/s | 6.3 cm | 16 single-swing limbs |
| Scrabber | 18.4 × 23.4 × 13.4 | 2.25 kg | 24 | 19.5 cm/s | 0.0 cm | 12 single-swing limbs |
| Ant | 4.7 × 10.1 × 10.8 | 0.06 kg | 24 | 17.1 cm/s | 0.5 cm | 6 double legs + body wave |
| Wormthing | 4.4 × 33.2 × 11.3 | 0.34 kg | 43 | 11.5 cm/s | 4.2 cm | 15 single limbs, long body |
| Twigger | 7.8 × 18.2 × 21.5 | 0.07 kg | 25 | 10.4 cm/s | – | 6 double legs, thin |

## 2. Parts

Every part is a **Blob**: a smooth rounded solid you can reshape. (The Zook Kit also offers
Cube and Sphere, but Zooks using them fail to load in contests, so only Blob is allowed.)

| Slider | Range | Effect |
|---|---|---|
| size.width / height / length | 0.1–3 | the part's x / y / z extent in units. `length` runs along the part, away from where it attaches |
| shape.pointiness | 0–1 | tapers one end (0.5 = symmetric) |
| shape.flatten_end | 0–1 | flattens the far end |
| shape.flatten_side | 0–1 | flattens one side |
| shape.squareness | 0–1 | 0 = rounded, 1 = box-like (use this for blocks, plates, feet) |
| look.rgb / brightness / texture | 0–1 | appearance only |

A Blob of size 1×1×1 is roughly a unit ellipsoid.

## 3. Building the tree

A Zook is a **tree**. The root (`body`) is the torso; it has no position or movement of its own.
Every other part hangs off a parent:

1. **Where it attaches** (`attach`). Picture a ray from the parent's centre in the direction
   (`around`, `elevation`). The child's base sits where that ray meets the parent's surface.
   - `around` (−180..180°): 0 = the parent's front (+z), ±90 = its two sides, 180 = its back.
   - `elevation` (−90..90°): 0 = the parent's "equator"; **positive = the lower half**,
     negative = the upper half; ±90 = directly below/above.
   - For a child of a child, these angles are in the *parent's* own frame (which may be rotated).
2. **Which way it points** (`rotate`). With all rotations 0, the child sticks straight out from
   the surface along that ray, with `length` running outwards. Then:
   - `up_down` (pitch): **positive tilts it downwards**, negative upwards.
   - `left_right` (yaw): swings it sideways.
   - `twist` (roll): spins it about its own length axis (matters for flattened/pointy shapes and
     for the direction a moving part swings).
3. The game lays the parts out from these numbers when you save (the Zook Kit's own code), so
   positions are always consistent. Parts can overlap visually; keep moving limbs clear of the
   body so they don't jam *(inferred: parts are solid bodies in the physics)*.

**Typical leg:** attach at `around` ±40..±140 (front to back along the side), `elevation`
20..50 (lower half), `up_down` 30..70 (angled down), long enough (`length` ≈ the body's
height + clearance) that its tip reaches below the body. Remember: whatever is lowest touches the
floor.

## 4. Mirroring

`"mirror": true` adds a twin on the other side: `around`, `left_right` and `twist` are negated,
and the part's whole subtree is copied with it. The part must not be on the centre line
(`around` must not be 0). The twin can have its own stride phase (`mirror_phase`), which is
how left and right legs alternate. Movement `side` Left/Right swaps on the twin automatically.

## 5. Joints and muscles

Every attached part connects to its parent with a two-axis joint (each axis limited to ±90°)
driven by muscles:
- `movement.stiffness` (1–10000): how hard the muscle holds or drives its target angle.
  The examples use 5000 for limbs. Low stiffness gives floppy parts that sag under weight.
- `movement.damping` (1–10000): resistance to fast motion (less jitter, slower response).
- Parts with `movement.type` "none" just hold their pose. A long, heavy, soft-jointed part will droop.

## 6. Movement: how Zooks walk

All moving parts share one **stride cycle**. Its speed is the root's `gait.cycle_speed`
(0–1; higher = faster strides). Each moving part has a **phase** (0–1): its position within
the cycle. Parts with the same phase move together; a difference of 0.5 is half a cycle.

### single: the part swings on its joint
The part's angle follows a closed loop of **directions**, its stride path (`movement.ik_points`).
Each point `[x, y, z]` is a direction in the part's own frame: z along the part (its rest
direction), y up, x sideways (mirrored automatically for twins). Only the direction matters,
not the length. The points are visited at equal time steps and the loop closes back to the first.
- If you give no path, the game's default is used:
  `[[0,0,2.37],[-0.53,0,2.31],[0,0.6,2.3],[0.56,0,2.3]]`, which means rest, swing to one side,
  lift, swing to the other side (about ±13° of swing and 15° of lift).
- For a leg sticking out of the body's side, its local x is roughly the body's forward/back
  direction and y is up. A stepping loop is "push back along the ground → lift → swing forward
  → put down". **Verified:** side legs (`around` ±45/90/135, `elevation` 30, `up_down` 50)
  with `[[-0.5,-0.1,2],[0.5,-0.1,2],[0.4,0.5,2],[-0.4,0.5,2]]` walk **forwards**, and the same
  path with the x signs flipped walks backwards. Bigger x = longer stride; bigger y = higher steps.
  If a Zook walks backwards in a sprint trial, flip the x signs.

### double: a two-part limb (thigh + shin)
Set `movement.type: "double"` on the **lower** part (the shin). Its parent (the thigh) bends
with it, and the knee is solved by inverse kinematics. Rules: the parent must not be the root
and must not move by itself, and a parent can have only one double child. Here `ik_points` are
**foot target offsets in the Zook's body frame** (x sideways, y up, z forward), in units,
relative to where the foot (the shin's tip) rests. They are *not* mirrored for twins, so keep x ≈ 0
for symmetric gaits. Default: `[[0,0,0],[0,0,0.5],[0,0.5,0],[0,0,-0.5]]` (rest → forward
0.5 → lifted 0.5 → back 0.5). Larger z swings = longer strides; larger y = higher lift.

### Steering (all contests steer you)
Contests point each Zook at a target. To turn, the engine scales down, and can reverse, the
strides of limbs on the inner side:
- root `gait.turn_sharpness` (0–1): how strongly the inner side is slowed (high = can reverse
  and spin on the spot).
- root `gait.turn_smoothness` (0–1): over how wide an angle to the target the turning blends in.
- `movement.side`: "Auto" (side taken from where the part attaches), "Left side"/"Right side"
  (force), or "Always" (never modulated: always full stride, so it doesn't help turning).
A Zook whose limbs are all on the centre line, or all "Always", can't steer.

### Targeting (heads, necks, tails)
`movement.targeting` "Normal" makes a part yaw **towards** the target, up to
`targeting_angle` degrees; "Inverted" makes it yaw away. It works on non-moving parts too,
e.g. a head or a front body segment that helps the Zook aim. (The root's
`gait.targeting_angle` is the Zook Kit's master setting for the same angle.)

### Body wave (worms, swimmers)
`movement.wave` (0–1) makes a part's joint sway side to side once per cycle, timed by its
`phase`. A chain of body segments with waves and staggered phases (0, 0.2, 0.4, …) slithers.
The Zook Kit's UI doesn't offer this, but the engine supports it (Series-1 Zooks use it).

## 7. Gait recipes

- **Quadruped trot** (diagonal pairs together): front leg `phase 0, mirror_phase 0.5`,
  back leg `phase 0.5, mirror_phase 0`.
- **Hexapod tripod** (very stable): front and back legs `phase 0 / mirror_phase 0.5`, middle
  legs `phase 0.5 / mirror_phase 0`.
- **Hopper / bounder:** all legs the same phase (untested here: try it with a sprint trial).
- **Worm:** 4–8 body segments in a chain (each a child of the previous one, attached at the
  back, `around` 180), each with `wave` 0.2–0.4 and phases stepping by 0.15–0.25.
- Study the example Zooks (table in section 1) with `get_zook`; Spider is the fastest and the
  best pusher, which makes it a good reference for a strong all-rounder.

## 8. What wins

- **Speed** (races, hurdles, lap): more stride per cycle (longer legs and stride paths),
  faster `cycle_speed`, low weight, and good steering so it doesn't zig-zag.
  `run_trial(…, "sprint")` measures it.
- **Pushing** (Sumo, Weakest Zook, Merry-Go-Zook): weight, a wide low stance, stiff legs
  that drive forward, a low centre of mass. Falling over or losing floor contact loses.
  `run_trial(…, "block_push")` measures pushing.
- **Obstacles** (hurdles, Smash, Dodgy Zook): leg clearance (lift in the stride path), enough
  height, and not getting stuck on edges.
- **Ball** (Zookball): the striker must push the ball straight into the goal without knocking
  it out; a broad, flat front helps *(inferred)*.
- Read each contest's rules with `get_contest`. The engine is deterministic for a given seed,
  so a change in result comes from your change.

## 9. Testing strategy: you choose every game

Test runs are **budgeted** (see `get_challenge`, usually 20), and every result tells you how
many are left. **Each run is exactly one game, and you decide which game it is** through the
tool's arguments. Picking the games that teach you the most is part of the challenge.

| Tool | One run = | You choose |
|---|---|---|
| `run_trial(ref, trial)` | one Zook Kit trial for one Zook | which Zook (`ref`), which trial: `sprint`, `hurdles`, `lap`, `block_push`, `high_jump`, or `free:<Env>` with a `duration` |
| `run_contest(contest, green, red, seed)` | one contest between two Zooks | which contest, which Zook plays green and which plays red, and the `seed` |

- **Opponents:** any example Zook (`Spider`, `Ant`, `Leapsa`, `Scrabber`, `Twigger`,
  `Wormthing`) or any of your own designs and versions (`MyZook`, `MyZook@2`). You can pit your
  new version against your previous one (`green="MyZook@4", red="MyZook@3"`) to check that a
  change is an improvement.
- **Seeds:** the same seed gives exactly the same game. Re-running the same game with the same
  seed teaches you nothing; change the seed to check that a result wasn't luck.
- **Free (no run used):** `save_zook`, `edit_zook`, `preview_zook`, `get_zook`,
  `validate_zook`, and all the reference tools. Look at the pictures before spending a run.
- **Colours:** in contests "green" and "red" are just the two arena sides. The final evaluation
  game in `get_challenge` says which side your submitted Zook plays, so test on that side.

A sensible plan for a 20-run budget:
1. Design, `save_zook` and check the pictures (free). Is it standing on its legs, with the legs
   pointing down?
2. `run_trial` sprint (1 run): does it walk forwards, how fast, does it drift or fall? If it goes
   backwards, flip the stride path's x signs (section 6) and re-test.
3. A trial that matches the challenge (e.g. `block_push` for pushing contests, `hurdles` for
   obstacles).
4. `run_contest` against the opponent you must beat, on your evaluation side and seed. If you
   lose, read the stats and stills (did you fall off, get pushed, walk the wrong way?), change
   one thing with `edit_zook`, and play the next game: against the target again, or new version
   against old.
5. Keep a few runs in reserve to confirm your best version on a different seed. Then
   `submit_design` it: that plays the single official evaluation game.

## 10. Limits

- Name: 1–25 characters (letters, digits, spaces, `_ - .`).
- Sliders outside their ranges are rejected by validation. The game also clamps anything it
  doesn't like and reports `clamped: true`.
- Textures: use names from the design guide's list. An unknown name only warns, and the part
  renders untextured.
- Every save is a new version (`Name@1`, `Name@2`, …); refs without `@` mean the latest.
