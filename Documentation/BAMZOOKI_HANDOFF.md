# BAMZOOKi AI Harness — Findings & Handoff

Handoff document for a Claude Code session taking over implementation. Everything in "Findings" was verified by hand against a real install (Zook Kit v3.1, Bonsai Engine build 158) unless marked *unverified*.

---

## 1. Goal

Build a **portable repo** that lets any AI model design BAMZOOKi creatures ("Zooks") through an API and **test them in the real game engine**, with results returned as data and contests **rendered live or to video**.

### Hard constraints

1. **No secrets or BBC material in the repo.** No encryption keys, passwords, RSA key files, decrypted scripts, game binaries, textures or example Zooks. The user supplies their own install; the tools derive everything they need from it at runtime (see §5).
2. **Hardware/OS agnostic.** It must work on Windows natively, and on Linux/macOS via Wine (Docker + Xvfb for headless). Make no assumptions about paths, GPU or user names; the install path is config.
3. **Real engine first.** Scoring must come from the original physics and contest logic (scripting the engine), not a re-implementation. A fast approximate simulator (e.g. MuJoCo) is a possible later add-on, not in scope now.

---

## 2. Install layout (Windows default: `C:\Program Files (x86)\BAMZOOKi\`)

```
Bonsai.exe                 engine (PE32, 2006); also expat.dll, MSVCP60.DLL, dsetup.dll
he.skx                     RSA private key used to decrypt scripts (see §4.3)
Build.txt
Scripts\*.ssx              Lua scripts (encrypted) — config + utilities
Scripts\Boot\*.ssx         boot scripts: Builder, Builder_anamorphic, ContestRunner, ContestFilePlayer
Scripts\Behaviours\*.ssx   contest behaviours (Winner, Loser, Timer, HeightCounter, ...)
Agents\Governor\<Name>\<Name>.sax   UI/system agents (Lua, encrypted)
Agents\World\<Name>\<Name>.sax      world/physics agents (Lua, encrypted), incl. Evo, Contest, BuilderParts
Agents\World\BuilderParts\*.dff     RenderWare meshes
ContestPacks\ContestPack1.dat       contest definitions (plain-text table, read by System.ReadTable)
Contests\Contest Pack\*.contest     12 contests (CP_ZOOK_SUMO, CP_ZOOK_SUPER_HURDLES, CP_ZOOK_TAG, ...)
Teams\examples\zook_list.tab
Teams\examples\Creatures\<Name>\<ver>.zook   Ant/2.1, Leapsa/58.57, Scrabber/196.195, Spider/2.1, Twigger/1.0, Wormthing/330.329
Teams\examples\Components\<name>             extension-less files that are Bonsai archives (part presets: "a blob", "crawl", ...)
NewTeam\                                     template copied to the save root's Teams/myZooks on first run
NewCreature\1.0.zook                         format "1.0" Zook (archive header "New Zook", CRLF text)
Fonts\*.met, many .png/.bmp textures
```

**Save root (verified, M0):** the engine's `HOME` label is `%APPDATA%` (e.g. `C:\Users\<u>\AppData\Roaming`). `Scripts/ProductConfiguration` creates the product directory `HOME/Bamzooki` and sets config `product_directory` to it. It holds `Teams/myZooks/`, `Contests/`, `Videos/` (`TempSaveVideo.bmv` while recording), `TeamSelection.dat`, `Video.cfg`, `passport_image.bmi`, `tempExpressed.zook`, and empty `Bonsai/Scripts` and `Bonsai/Agents/{Governor,World}` folders (see the soft-boot note in §3). `MYDOCUMENTS` is `Documents`; saved motion files go to `Documents/My BAMZOOKi/Motion Files/*.bvz`. No VirtualStore redirection was seen. The engine error log is `%APPDATA%\BonsaiErrorLog.txt`, written only when an error occurs and overwritten each time.

**Zook naming rule:** files are named `<major>.<minor>.zook`. The examples use versions such as 2.1 and 58.57, so `1.0.zook` is only what a new Zook gets, not a requirement. The **Zook's name is its folder name**: `.../Creatures/<ZookName>/<ver>.zook`. *(Corrected in M0: the earlier "must be called 1.0.zook" was too strict.)*

---

## 3. Engine facts

- **Bonsai Engine** by Gameware Development ("Creature Labs"), build 158, dated 7 Apr 2006. Graphics: **RenderWare 3.4**. Physics: **MathEngine Karma** (the exe has Karma strings and `Physics_Sim_*` config). Crypto: **Crypto++**. Compression: **zlib**.
- The game is **almost entirely Lua scripts** run by `Bonsai.exe`. **Verified M0: the dialect is a patched Lua 4.0** (`_VERSION == "Lua 4.0"` at runtime, and the exe has "Lua 4.0"). Rules for writing scripts:
  - No `local function`, no `pairs`/`ipairs`/`pcall`/`globals`/`dofile`. Iterate with `for k,v in t do` or `next`. Nested functions can't see outer locals (Lua 4 upvalues need `%name`), so use globals.
  - The build adds a `false` value. **A condition that evaluates to `nil` is a runtime error** ("condition evaluated to nil"), so write `while k ~= nil do`, not `while k do`.
  - `[[...]]` long strings misbehaved inside `dostring` (not investigated further). Keep code in separate files.
  - Base functions present: `call assert dostring error foreach foreachi getn next print sort tinsert tremove tonumber tostring type _ALERT _ERRORMESSAGE`, plus engine `Trace Execute ExecuteString SetPoolSize`. String functions live under `String.*`, maths under `Number.*`.
  - Global API tables in a **boot** VM: `Agent Attributes Bin Config Debug Matrix Number Quatn Segment Spot String System Table Vector World`. `Genome`, `Camera`, `GUI`, `Visual`, `Input`, `Sound`, `UI` and others are **nil in the boot VM**. They're component tables that only exist inside agents declaring them (`# X specializes Y embeds Visual, Input`). `tools/spike/gen_probe.py` dumps the full member lists.
  - Useful extras: `String.ReadFile(path)` / `String.WriteFile(path, text)` (read or write any text, so results can be written as JSON directly), `Bin.ReadFile/WriteFile/ToString/DecryptScript`, `Debug.GetGlobals`, `System.Exit/BrutalAbort/GetHostname/DateTimeString`.
- **Agents ("Munki" classes):** `.sax` source starts with a header line `# Name specializes Base [final] [embeds A, B]`. Each agent has its own VM, and globals aren't shared. `Agent.CreateClassFromString(source_string)` defines a class at runtime (it needs a **string**; a `Bin` is rejected). A subclass inherits the parent's `Initialize` and other functions, and can override any `Message*`/`Timer*` handler. `Agent.SetTimer("Name", agent, "id", seconds)` calls `TimerName()`. `Agent.SendMessage`/`PostMessage("Name", agent, [delay,] ...)` call `MessageName(...)`.
- **Script errors** are logged to `BonsaiErrorLog.txt` with file, line and function, then the engine shows a modal error dialog and **hangs**. Always run it with a timeout and kill it.
- **Trace output (verified M0):** `Trace(...)` goes to **`OutputDebugString`**, prefixed `[ munki ]`. Engine messages are prefixed `[ engine release ]`/`[ engine error ]`. Capture it with a DBWIN listener (`tools/spike/engine.py run --trace FILE`) or DebugView. Nothing goes to stdout, and `-debug` / `-luastack` made no visible difference for simple scripts. Config `trace_behaviours = 1` makes the BehaviourManager trace every event, which is very useful.
- **The engine only runs while its window is in the foreground (corrected in the headless experiments; an earlier note in this doc blamed a stdout pipe).** When the window loses focus, the main loop slows down or stops completely, and posted messages and timers don't fire. In repeated runs, the engine window's share of foreground samples predicted the outcome: 1/80 and 10/80 stalled, 21/45 finished at ×0.40, ≥40/41 finished at ×0.7–0.85. The harness should bring the window to the front itself (`engine.py --keepalive foreground`, which uses `AttachThreadInput` + `SetForegroundWindow`). **Never use the "tap Alt" trick to pass the foreground lock:** Alt puts the engine window into system-menu mode, a modal loop that freezes the engine. Faking `WM_ACTIVATEAPP`/`WM_ACTIVATE` or pumping `WM_NULL` is inconclusive. Still keep stdout/stderr off a pipe (DEVNULL or a file) as a precaution.
- **Speed:** sim time runs at about real time or a bit slower with rendering on (×0.7–0.9 when focused), so a 1-minute contest takes about a minute. Boot to first sim tick takes about 2 s, and a hello-world boot and exit takes about 3–4 s. Faster-than-real-time was not pursued: model design time is the bottleneck, not simulation.
- **Determinism (verified):** with a fixed random seed the simulation is **deterministic in sim time**. Three Sumo runs (Ant vs Spider, seed 1, 15 s of sim) ended with identical Zook positions to 3 decimals, even though wall-clock speed varied from ×0.29 to ×0.81 and one run lost focus midway. So physics uses a fixed step, not the frame time. Without a seed, results differ between runs: `Scripts/PhysicsConstants` calls `Number.randomseed(System.AbsoluteTime())`, and behaviours such as `Follow`/`RandomMove` call `Number.random()`. **The harness must call `Number.randomseed(seed)` after `Execute("PhysicsConstants")`** (`harness_boot.lua` does this with the job's `seed`). Setup is deterministic even without a seed: positions at Go matched across all runs.
- **`-videocfg <name>`** reads `HOME/Bamzooki/<name>` (default `video.cfg`): `windowed`, `resolution_x/y`, `bit_depth`, `quality`, `shadow_quality`. The user's `Video.cfg` was fullscreen 1920×1080, so the spike uses its own `aiharness.vcfg` (windowed 800×600) in the save root.
- **Launch command lines** (from the installed shortcuts):
  ```
  Bonsai.exe -init builder           -videocfg builder.vcfg           -splash ".\BAMZOOKi Zook Kit Splash"
  Bonsai.exe -init contestrunner     -videocfg contestrunner.vcfg     -splash ".\BAMZOOKi Simulator Splash"
  Bonsai.exe -init contestfileplayer -videocfg contestfileplayer.vcfg -splash ".\BAMZOOKi Motion Player Splash"
  ```
  `-init <name>` selects `Scripts/Boot/<name>.ssx`, so **a custom boot script = `-init <ourname>`**. **Verified M0:** a boot script re-encrypted with the public key derived from `he.skx` runs, and `ROOT` is the folder of the `Bonsai.exe` that was launched. A full **copy** of the install (17 MB) works, so the real install never needs to change. The exe refuses script paths outside the script directory ("Drive \ and .. not permitted").
- Other command-line flags found in the exe: `-debug`, `-luastack`, `-safe` (sets `safe_mode`), `-monitor`, `-studio`, `-splash`, `-videocfg`. The Motion Player boot script also parses `-f <file.bvz>` from `System.GetCommandLine()`. Unknown flags are ignored, so boot scripts can define their own (the spike loader uses `-script <file>`).
- **Path labels** in the exe: `%ROOT%`, `%SCRIPTS%` = `%ROOT%/Scripts/` (boot scripts in `Boot/`), `%ALLAGENTS%` = `%ROOT%/Agents/`, `%WORLDAGENTS%`, `%GOVERNORAGENTS%`, `GENOMES` = `%ROOT%/Agents/World/Evo/`, `%HOME%` (`%HOME%\Worlds\`), `MYDOCUMENTS`, `EXPORT`. Read them at runtime with `System.GetLabeledPath("HOME")` etc.
- **`script_extension`** (config key, parsed next to `-safe`/`-luastack`): **not needed**. `Boot/hello.lua` is not found ("file does not exist"), and plain text named `hello.ssx` fails ("Unknown error from VM"). The workaround is better anyway. **One** encrypted loader boot script (`tools/spike/lua/loader.lua`) runs `dostring(String.ReadFile(path))` on a plain-text file given with `-script <path>`, which may live anywhere, such as the repo. Everything else (boot logic and agent classes via `CreateClassFromString`) can stay plain text, so the only generated encrypted file is the loader.
- **Soft boot hook:** `Scripts/SoftConfiguration` decrypts and runs `HOME/Bamzooki/Bonsai/Scripts/<product>_init.ssx` and the registry key `<product>_soft_boot`, but **no boot script calls SoftConfiguration**, so it's dormant.
- **Lua APIs** the scripts use (useful for our boot script):
  - `System.*`: MainLoop, ExitMainLoop, GetCommandLine, ReadTable, WriteTable, ReadArchiveTable, WriteArchiveTable, Zip, Unzip, GetDirectoryContents, IsValidPath, CreateDirectory, DeleteFile, RealTime, AbsoluteTime, GMDateTimeString, UsingEncryption (always returns 1), IsArchive, GetMachineId, BuildNumber
  - `Agent.*`: Create, Destroy, SendMessage, PostMessage, SetTimer, StopTimer, Me, Zap, IsValid, CreateClassFromString
  - `World.*`: Create, Destroy, OpenAccess, CloseAccess, SimTime, Pause, Resume, IsPaused, OpenVisualOutput, WriteVisual, CloseVisualOutput, OpenVisualInput, ReadVisual, SeekVisual
  - `Genome.*`: Load, LoadDirectToTable, Save, SaveArchived, SetFromTable, GetAsTable, Express, ReExpress, **Mutate**, ModifyBodySolid
  - `Camera.*`: Create, SetPosition, SetTarget, SetViewAngles, GetImageAsString, **SaveAsJpeg**, Resize, SetFog, SetBluescreen
  - `Config.Get` / `Config.Set`, `Trace(...)` for logging

---

## 4. File formats (all verified)

### 4.1 Bonsai archive container (`.zook`, `.bmi`, `Registry.sdx`)

```
"Bonsai Engine - Archived information file. Version: 000000001. \n"
<user header text>  "\n"
00 01 00 00 00                      (5 bytes, constant in every file seen; meaning unknown)
<payload>
```

- `.zook` user header = `"\nThis zook file is not compatible with this software. Please upgrade your software."`. It is a decoy message for older versions, set by config `genome_archive_header`. **Not universal:** `NewCreature/1.0.zook` (format `<zook version = "1.0">`) has the header `"New Zook"`. Readers must accept any header, and writers should preserve it.
- Extension-less `Teams/*/Components/<name>` files are also Bonsai archives. `tools/extract.py` decodes all 36 encrypted and 2 plain archives in the install and save root.
- `GenomeConfiguration` comments that the Zook password may be set to `false` "to only load unarchived ascii files". That could enable plain-text Zooks for a patched config (*untested*).
- `.bmi` (passport image) user header = `"Image Version 1.0"`.
- **Payload:**
  - If an encryption key is given: **Blowfish-ECB(key)** over a **zlib stream**, zero-padded to a multiple of 8 bytes. The engine errors with `BA0008 FIle is not encryption aligned` otherwise.
  - If the key is `""`: the payload is the zlib stream alone (this is how `.bmi` images are stored).
- The engine reads in 4 KB chunks, decrypting then inflating. Trailing padding after the zlib stream is harmless.

### 4.2 Zook content (decrypted, decompressed)

XML-like text, `<tag attr = "value" >` style, with attributes in alphabetical order:

```
<zook version = "2" >
  <passport>
    <ownership><owner adoption_date uid username zookname notes>
        <modifications> CREATIVE_MOD / PHYSICAL_MOD / COSMETIC_MOD / DYNAMIC_MOD percentages
    <details> detail name=Height|Length|Width|Weight|Components, data, comment (units)
  <photo_album>   snapshot image as hex text (makes saved files ~400 KB decoded)
  <parameters>
  <genome>
    <bodysolid ...>  one per body part (12 in the test Zook). Attributes include:
        name, partId, mesh (e.g. "Blob"), texture (e.g. "cheetah.bmp"),
        scalex/scaley/scalez, pitch/yaw/roll, phi/theta, colour, colour_red/green/blue (0..1), brightness,
        cubosity, flatness, asymmetry, bias, mirror_group, segment, moveable,
        muscle_stiffness, muscle_damping, damping, leg_phase, max_spine_angle, min_spine_target_angle,
        zook_speed, turn_sharpness, turn_smoothness
      <attribute_groups> (Shape, Master IK Control, Colour, ...)
      <modifications><modifier owner_uid username zookname>...
    <cardanconnector ...>  joints between parts (11 in the test Zook), with <ik_positions><point .../>
```

- **Passport stats** (height, weight and so on) are probably computed by the Builder. Check whether hand-written values matter, or whether the engine recomputes them on load or in contests.
- **Valid ranges** for every slider are defined in the Builder/BuilderParts scripts. Extract them into the SDK's schema.
- **Verified in game:** editing a colour and the name, re-encoding and loading works (a "RedHead" test Zook rendered with a red head).

### 4.3 Scripts (`.ssx`, `.sax`)

- No header. The file size is always a multiple of 64.
- **RSA-OAEP (SHA-1, MGF1-SHA-1), 512-bit key**, applied per block: each 64-byte ciphertext block yields **22 bytes** of plaintext. The concatenated plaintext is a **zlib stream** of the Lua **source** (comments included).
- The private key is `he.skx` in the install root: PKCS#8 DER, e = 17. The engine loads it (string `he.skx`) and decrypts scripts through a custom stream reader.
- **Re-encryption** (for patched or custom scripts): zlib-compress the source, split it into 22-byte chunks, OAEP-encrypt each with the public key derived from `he.skx`. **Verified in game (M0):** re-encrypted boot scripts load and run. The engine accepts our zlib level-9 stream, and OAEP's random padding doesn't matter.

### 4.4 Other formats

- `ContestPacks/*.dat`, `default.dat`, `zook_list.tab`, `TeamSelection.dat`: plain-text tables (`"key" = value`), read and written by `System.ReadTable` / `WriteTable`.
- **Recordings:** a contest is recorded with `World.WriteVisual()` on a timer into `TMP_VIDEO_PATH/TempSaveVideo.bmv`, then `System.Zip(..., file.bvz, 6)`. `.bvz` is the zipped `.bmv`. The Motion Player unzips and replays it. The `.bmv` format itself is undocumented and is a candidate for later decoding to render outside the engine.
- `.dff`: RenderWare meshes. `.bmi`: passport images, a 256×256 RGB hex string in an archive table (`"image" = "080808 0001 0001 <rgb hex>"`).

---

## 5. Secrets handling (repo must stay clean)

There are two secrets, and **both must be derived at runtime from the user's own install, never committed**:

1. **Script key:** load `<install>/he.skx` directly.
2. **Zook password:** decrypt `<install>/Scripts/GenomeConfiguration.ssx` with (1), then extract the string argument of `Config.Set("genome_rhesus_macaque", "...")` with a regex. **Skip commented lines:** a `--~` commented line and an inline comment repeat the setting (`bamzooki.install.find_config_string` does this).
   - Similarly, `registry_key` (protects `Registry.sdx`) is set in `Scripts/Boot/Builder.ssx` (verified; derived the same way). It's only needed if the harness has to read or write the registry settings file.

**Implemented (M0):** `bamzooki.install.Install` (discovery and keys), with the guard in `bamzooki.guard` / `tools/check_assets.py`. The guard runs in pre-commit, CI and pytest. It rejects game file types (`.ssx .sax .skx .zook .dff .bmi .bmv .bvz .sdx .contest`) and any file that starts with the Bonsai archive magic. When an install is available it also rejects the derived Zook password and registry key (raw, lowercase, hex, base64) and the `he.skx` bytes. CI has no install, so CI only runs the file-type and content checks. The local pre-commit hook does the secret scan.

Implementation rules:

- Place decrypted scripts and any extracted material in a **gitignored** cache (e.g. `.cache/bamzooki/`).
- Add a test or pre-commit check that fails if the repo contains `.ssx`, `.sax`, `.skx`, `.zook` or `.dff` files, or any 14+ character string matching the derived password. Keep test fixtures synthetic, built at test time from the user's install.
- Do not ship decrypted BBC scripts. Patched scripts should be distributed as **diffs or generated code** applied locally at setup time.
- Legal note: "discontinued" doesn't by itself make something fair use (UK fair dealing is narrower than US fair use). Keeping the repo free of BBC code, assets and keys, and requiring the user's own copy, is the safest design, so stick to it.

---

## 6. Key script locations (decrypt locally to read them)

| File | What's there |
|---|---|
| `Scripts/GenomeConfiguration.ssx` | Zook encryption password (`genome_rhesus_macaque`), versions, archive header |
| `Scripts/Boot/ContestRunner.ssx` | Simulator boot: config, then `Agent.Create("SimpleFrame")`, `Agent.Create("ContestRunner")`, `System.MainLoop()`. **Template for our headless boot script.** |
| `Scripts/Boot/ContestFilePlayer.ssx` | Motion Player boot; parses `-f`, unzips `.bvz`, `VisualFilePlayer` → `OpenFileNoUI` |
| `Agents/World/BuilderParts/BuilderParts.sax` | `SaveGenome` / load (`Genome.SaveArchived`, `Genome.LoadDirectToTable`) |
| `Agents/Governor/ContestRunner/ContestRunner.sax` | `ReadContestDefinitions()` (loads `ContestPacks/*.dat`), `RunContest()`: builds `contestants[i] = {fullname = team.."/Creatures/"..creature.."/"..version..".zook"}` then `SendMessage("BeginContest", myContest, name, contestDef, contestants)` |
| `Agents/Governor/ContestRunnerContest/ContestRunnerContest.sax` | `MessageBeginContest`, `BeginRound` (creates the world, `SplitScreen` camera, `Contest` world agent, lights; `Go` at sim time 4), `MessageBehaviourReport`, `MessageEndRound`, recording (`TimerUpdate` **always** starts `World.OpenVisualOutput(TempSaveVideo.bmv)` after sim time 1; `TimerRecord`; `RecordThenGo`). **Correction (M0):** the live `MessageBehaviourReport` only updates on-screen score text, and only when `contestDef.score` is set, which `ContestPack1.dat` never does. The round-ending logic (`finish` → `EndRound`) lives in `MessageBehaviourReportOld`, which is **unused**. In the shipped Simulator a contest only ends when the user clicks Stop, and nothing reacts to `finish`. |
| `Agents/World/Contest/Contest.sax` | `MessageContestantsSelected`, `InstantiateAll`, `Go`, `BehaviourReport` (adds `scoreboard.contestant[i]` contestant indices and `description[i]` to the scoreboard, then posts it to the notify agent) |
| `Agents/World/BehaviourManager/BehaviourManager.sax` | Runs a contest's `behaviours` graph from the `.contest` file (`triggerOnEvents` etc.). The `Finish` behaviour sends `{name="finish", scoreboard={player, score}}` up to `Contest`. |
| `Scripts/Behaviours/*.ssx` | Win/lose and scoring rules per contest |
| `Agents/World/Evo/Evo.sax` | Evolution-related world agent (`Initialize(pos, rot, genomeFile, ...)`, damage/collision). Investigate: combined with `Genome.Mutate` there may be a built-in evolution path. |
| `Agents/Governor/Photographer/*.sax` | Renders Zook snapshots (`Camera.GetImageAsString`), useful for vision feedback |
| `Scripts/AccessCodedTableKey.ssx` | Encrypted table read/write helper (`Registry.sdx`) |

---

## 7. Objectives & milestones

### M0: Setup & verification
- [x] Config: install path, save-root path, and Wine command (`bamzooki.toml`, env overrides; see README).
- [x] `tools/extract.py`: derive keys (§5), decrypt all 116 scripts into the gitignored cache, decode all 7 Zooks plus the other archives.
- [x] Lua version: **Lua 4.0 (patched)**. `script_extension` was not needed: re-encrypted boot scripts load, and an encrypted loader runs plain-text Lua (§3).
- [x] `Trace` output goes to **OutputDebugString** (§3).
- [x] Minimal headless contest (no selection UI): `tools/spike/lua/contest_spike.lua` + `AIHarnessContest.sax.lua` ran **Zook Sumo, Ant vs Spider**, caught `finish`, wrote the scoreboard with `System.WriteTable` and exited (code 0) in about 9 s. Two runs gave identical scoreboards. **Caveat:** Sumo's `finish` fires at sim time ~4.0, right after Go, because its `NoContact` behaviours use `timeout = 0`, which triggers on the first update tick (see §8). With `stop_on_finish = 0` the bout ran its full 30 s of sim time without problems.
- [ ] Confirm the engine runs under Wine (Linux), then under Docker + Xvfb. *(Not attempted; deprioritised, see below.)*

#### M0 follow-up: headless experiments (outcome: run on screen)
The user's priority is to **watch the AI's contests on screen**, and model think time dominates, so a visible window at about real time is fine. Findings from `tools/spike/experiments.py` (job-driven `harness_boot.lua` + `AIHarnessDriver.sax.lua`):

| Mode | Result |
|---|---|
| Window + SplitScreen camera + lights | Works. Use this. Without lights (`Visual.CreateLight`) the scene renders **black**. |
| Window, no camera | Contest never instantiates ("Unknown error in function Initialize"). Visual-embedding agents (Zooks, targets, tables) need a camera in the world. |
| No SimpleFrame, one 2×2-pixel `SplitCamera` | Works (×0.85). `SplitCamera` needs a `SetView` message, or its `UpdateView` errors. The exe still creates its own bare window ("Bonsai Engine, Build: 158…"). There's always a window. |
| No window agent, no camera | Same failure as "no camera" |
| Hidden desktop (`CreateDesktop`) | "Error Initializing Graphics": Direct3D needs the visible desktop |
| Launched minimized | "Error Initializing Graphics" |
| Window not in foreground | Stalls or slows (see §3) |

Conclusions: on Windows, true headless isn't possible without patching the exe. Run on screen, bring the window to the front, and use a fixed seed. If unattended batch runs are needed later, try Linux + Wine + Xvfb (one virtual display per engine, where the window is always focused) or a dedicated Windows VM or session.

Spike lessons for agent code: don't define `Finalize` in a runtime class (it broke class instantiation). Destroy the world yourself before `System.ExitMainLoop()`, or the engine shows an error dialog on exit. Governors see world agents (e.g. `Agent.GetAgentsOfClass("Evo")`) **only inside `World.OpenAccess(world)`**. A `Visual`-embedding governor agent can't be used without a camera in the world.

#### M0 spike: how to reproduce
```
python tools/spike/engine.py backup            # save root + error log → .cache/bamzooki/backup/<ts>
python tools/spike/engine.py sandbox           # copy install → .cache/bamzooki/sandbox/install
python tools/spike/engine.py put tools/spike/lua/loader.lua loader     # the only encrypted file
python tools/spike/engine.py run --timeout 120 --trace .cache/bamzooki/logs/t.txt -- \
    -init loader -videocfg aiharness.vcfg -script C:/abs/path/tools/spike/lua/contest_spike.lua
```
`aiharness.vcfg` must exist in the save root (windowed 800×600). Results go to `<sandbox>/contest_out.dat` and the spike logs.

### M1: Zook SDK (Python)
- [ ] Codec: archive container read/write (§4.1), key passed in.
- [ ] Parser/serializer for the genome XML that round-trips byte-identically on all example Zooks.
- [ ] A high-level JSON schema for models (parts, joints, mirroring, colours, movement params) → genome. Validate against ranges extracted from the Builder scripts.
- [ ] Writer that creates `Teams/<team>/Creatures/<Name>/1.0.zook` and updates `zook_list.tab` if required.
- [ ] Decide on the passport/photo: strip it, keep it, or generate it.

### M2: Contest runner (script the real engine, on screen)
*Prototype exists:* `tools/spike/lua/harness_boot.lua` + `AIHarnessDriver.sax.lua` + `tools/spike/experiments.py` already do most of this. Productise them into `bamzooki.runner`.
- [ ] Boot via the encrypted `loader` + plain-text harness (no per-change re-encryption). The job file (Bonsai table, `bamzooki.codec.table`) holds contest, Zook paths, `seed`, `max_sim_time`, `stop_on_finish`, camera mode, record flag and output paths.
- [x] Load contest definitions as `ReadContestDefinitions` does and drive `Contest` directly with no selection UI *(spike)*.
- [x] Hook `BehaviourReport` / round end; record reports, timing samples and final Zook positions *(spike)*.
- [ ] Decide win conditions per contest (see §8: `finish` is unreliable; e.g. Sumo's `NoContact timeout = 0`).
- [ ] Python `run_contest(contest, zook_a, zook_b, seed=…, record=False) -> Result`: write the job, launch the engine, **bring its window to the front**, wait with a timeout, and parse the result. Kill it on timeout, because errors raise modal dialogs.
- [x] ~~Faster-than-real-time / no rendering~~: not needed (contests are about a minute; model think time dominates). Headless isn't possible on Windows (M0 follow-up).
- [ ] One engine at a time per desktop, since it needs the foreground. Parallelism (Wine/Xvfb or VMs) is optional later.
- [ ] Focus handling: give the window focus at launch (done in the spike), and optionally re-take it during a game when the user has been idle for a few seconds (`GetLastInputInfo`), so the user's own clicks aren't fought but a stalled game resumes.

### M3: Rendering (live + video)
- [ ] **Live:** run M2 with rendering on. The engine draws the contest in its own window.
- [ ] **Recording:** enable the `WriteVisual` recording path to produce `.bvz`, so contests can be replayed in the Motion Player (`-init contestfileplayer -f <file>`).
- [ ] **Video (MP4):** choose the most portable option:
  1. In-engine frame dump: a timer calls `Camera.SaveAsJpeg` each frame during playback, then ffmpeg assembles the frames. Deterministic and hardware agnostic. *Preferred if it works.*
  2. Screen capture of the engine window: ffmpeg `gdigrab` on Windows, `x11grab` on Xvfb.
- [ ] Camera control for nicer shots (`Camera.SetPosition` / `SetTarget`; `SuperCam` and `SplitCamera` agents already exist).

### M4: Model-facing API
- [ ] MCP server (plus a plain Python/CLI API) with tools such as `list_part_types`, `list_contests`, `create_zook(spec)`, `modify_zook`, `validate_zook`, `render_preview` (Photographer snapshot for vision models), `run_contest`, `get_result`, `get_video`.
- [ ] Contest documentation for models: rules per contest, extracted from the contest pack and behaviours.

### M5: Competitions & search (later)
- [ ] Tournaments between models across the 12 contests, with Elo ratings, saved replays and videos.
- [ ] Hybrid search: the model proposes a body plan and an optimiser (e.g. CMA-ES) tunes the numbers, using `run_contest` as the fitness function.

---

## 8. Open questions / unknowns

- The meaning of the 5-byte marker `00 01 00 00 00` (possibly flags + version).
- Whether passport `details` and `modifications` are validated or recomputed; whether `uid` matters for loading or contests.
- ~~Whether contest scoring is deterministic.~~ **Yes, with a fixed seed** (§3). Still to check: other contests, and Zooks with more complex neural controllers.
- In Sumo, both example Zooks end about 24 units below their start (y ≈ −24) within 15 s, at x ≈ 18–25. Find out whether they fall off the table, whether coordinates are scaled (`contest_unit_scale = 40`), or both. Watch it on screen.
- `injection_listen_port` (default **3030**), `monitor_socket`, `performance_socket`, `rmt_server` and `visual_broadcast` exist in the exe. "Injection" may mean sending Lua into a running engine over TCP, which would allow live control without restarting. *Unverified; worth a look for M2/M4.*
- **Contest win conditions are unreliable as shipped.** Nothing in the consumer Simulator acts on `finish` (§6), and at least Sumo's `NoContact` (`timeout = 0`) fires immediately. For M2, decide per contest whether to (a) patch behaviour params at load time (e.g. a non-zero `NoContact` timeout), (b) ignore `finish` and judge from world state at a fixed sim time (positions, contacts), or (c) both. Survey all 12 contests' behaviour graphs first.
- ~~Whether the engine can run without a visible window.~~ **No** (on Windows); see the M0 follow-up table. Deprioritised.
- Capturing frames: `PrintWindow` on the engine window gives a blank (blue) 3D view. `SplitCamera` already has a built-in frame dump: when `myRecordingImages` is set, `SystemCamera` calls `Camera.SaveAsJpeg("Movies/Tmp<n>.png")` every frame. A subclass can switch it on, and ffmpeg can assemble the frames (M3). Desktop capture (`ffmpeg -f gdigrab`) is the fallback.
- Wine compatibility of RenderWare 3.4 / DirectX on the target machines.

---

## Appendix A: Reference codec (Python, `cryptography` package)

The key is passed in; never hard-code it.

```python
import zlib, re
from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives.serialization import load_der_private_key
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

MAGIC = b"Bonsai Engine - Archived information file. Version: 000000001. \n"
MARKER = b"\x00\x01\x00\x00\x00"
OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)

# --- archives (.zook / .bmi) ---
def archive_decode(data: bytes, key: bytes) -> tuple[bytes, bytes]:
    """Returns (user_header, plaintext). key=b'' for unencrypted archives."""
    assert data.startswith(MAGIC)
    i = data.index(MARKER, len(MAGIC))          # assumes MARKER doesn't occur inside the header text
    header = data[len(MAGIC):i].removesuffix(b"\n")
    body = data[i + len(MARKER):]
    if key:
        body = Cipher(Blowfish(key), modes.ECB()).decryptor().update(body)
    return header, zlib.decompressobj().decompress(body)

def archive_encode(header: bytes, plaintext: bytes, key: bytes) -> bytes:
    body = zlib.compress(plaintext, 9)
    if key:
        body += b"\0" * (-len(body) % 8)
        body = Cipher(Blowfish(key), modes.ECB()).encryptor().update(body)
    return MAGIC + header + b"\n" + MARKER + body

# --- scripts (.ssx / .sax) ---
def load_script_key(install_dir):
    return load_der_private_key(open(f"{install_dir}/he.skx", "rb").read(), None)

def script_decode(data: bytes, rsa_key) -> bytes:
    raw = b"".join(rsa_key.decrypt(data[i:i + 64], OAEP) for i in range(0, len(data), 64))
    return zlib.decompress(raw)

def script_encode(source: bytes, rsa_key) -> bytes:
    comp, pub = zlib.compress(source), rsa_key.public_key()
    return b"".join(pub.encrypt(comp[i:i + 22], OAEP) for i in range(0, len(comp), 22))

# --- derive the Zook password from the user's install ---
def derive_zook_key(install_dir) -> bytes:
    k = load_script_key(install_dir)
    src = script_decode(open(f"{install_dir}/Scripts/GenomeConfiguration.ssx", "rb").read(), k)
    m = re.search(rb'Config\.Set\(\s*"genome_rhesus_macaque"\s*,\s*"([^"]+)"', src)
    return m.group(1)
```

The `.zook` user header is `b"\nThis zook file is not compatible with this software. Please upgrade your software."` (note the leading newline). Verified: decode → encode → decode is byte-identical on the plaintext, and a re-encoded edited Zook loads in the Zook Kit.

## Appendix B: How the format was cracked (context)

1. The `.zook` payload had near-maximum entropy, with repeats at periods 8/24/56/88 aligned to 8-byte blocks. That suggested an 8-byte block cipher in ECB mode over a compressed stream.
2. The `.bmi` used the same container but no encryption, revealing the zlib + text-table layout.
3. `Bonsai.exe`: Blowfish tables plus Crypto++ RTTI names; the archive reader (decrypt 4 KB chunks, then inflate) found via its `BA000x` error strings. The key always arrives as an argument from Lua, so it isn't in the exe (brute-forcing all exe strings confirmed this).
4. Scripts: sizes were multiples of 64, the exe referenced `he.skx`, and `he.skx` turned out to be a PKCS#8 RSA-512 private key. RSA-OAEP-SHA1 per 64-byte block gave zlib, then Lua source.
5. `GenomeConfiguration.ssx` sets the Zook password in config `genome_rhesus_macaque`, with a comment calling it the encryption password.

## Appendix C: Engine reference (Bonsai / Munki / Karma)

Collected during M0 from the exe (strings, imports, a little disassembly), the decrypted scripts and runtime probes. Facts are marked *verified* (observed at runtime), *source* (read in the scripts or exe) or *guess*.

### C.1 Technology stack
| Layer | What | Evidence |
|---|---|---|
| Engine | **Bonsai Engine**, Gameware Development, build 158 (exe dated Apr–May 2006) | `Build.txt`, version string |
| Scripting | **Munki**: a patched **Lua 4.0** with an agent/class system on top | *verified* (`_VERSION`, probes) |
| Graphics | **RenderWare 3.4** (Criterion) on Direct3D 8/9 (`d3d8.dll`, `d3d9.dll`; config `use_dx9`); shader-model strings `vs_1_0…ps_3_sw` | imports, strings |
| Physics | **MathEngine Karma**: rigid bodies, joints and contacts. MathEngine (Oxford, UK) sold Karma around 2000–03; Unreal Engine 2 used it for ragdolls and vehicles; Criterion bought it in 2003 (it became part of RenderWare Physics). Log channel `[ engine karma ]` (SSE autodetect at startup). | strings, trace |
| Input/sound | DirectInput (a hidden "Bonsai DirectInput Feeder" window), DirectSound | imports, window list |
| Other libs | Crypto++ (RSA-OAEP, Blowfish), zlib, expat (XML), WinINet (online features), sockets | RTTI, imports |

### C.2 Runtime model
- **Process:** `Bonsai.exe -init <boot>` runs `Scripts/Boot/<boot>.ssx` in a boot VM. A long-running app calls `System.MainLoop()`, which returns after `System.ExitMainLoop()`; the boot script then cleans up and the process exits. The exe always creates a main window (class `BonsaiSingleFrame`); the `SimpleFrame` agent only titles and manages it. **The loop only progresses while that window is in the foreground** (§3). The runner gives it focus at launch, so the user never has to click it. Clicking elsewhere during a game pauses it until focus returns.
- **Agents:** instances of Munki classes. **Governor** agents (`Agents/Governor`: UI, controllers) live outside worlds. **World** agents (`Agents/World`: Zooks = `Evo`, arena props, cameras) live inside a `World` and may only be touched by a governor between `World.OpenAccess(w)` and `World.CloseAccess()`. Each agent has its **own Lua VM**: globals aren't shared, and data moves through message arguments (tables are copied).
- **Class header:** `# Name specializes Base [final|abstract] [embeds C1, C2]`. Inheritance chains are used (e.g. `Evo → BaseTarget → Selectable`). `Agent.CreateClassFromString(src)` defines classes at runtime.
- **Components (`embeds`)** give an agent extra API tables. Counts across shipped agents: `Visual` 46, `Input` 37, `Karma` 29, `Camera` 14, `GUI` 13, `Sound` 6, `UI` 3, `Genome` 2, `XML` 1, `Socket` 1, `Neural` 1 (only `Evo`). They are **nil** in the boot VM. `Visual` agents need a camera in their world (a contest won't instantiate without one).
- **Callbacks by naming convention:** `Initialize(...)` on create; `Message<Name>(...)` for `Agent.SendMessage("<Name>", agent, ...)` (synchronous, returns a value) and `Agent.PostMessage("<Name>", agent, delay, ...)` (deferred); `Timer<Name>()` for `Agent.SetTimer("<Name>", agent, "<id>", seconds)` (stopped with `Agent.StopTimer(agent, "<id>")`); engine events `SystemCamera(frametime)` (per rendered frame, camera agents), `SystemKeyDown(key)`, `SystemUIChange(segment)`, `SystemUIResize(segment)`.
- **Worlds:** `World.Create([flags])`. Contests use `World.Create()`; previews (Builder, Zook preview, Motion Player) use `World.Create(2)`. World agents check `World.GetFlags() == 3` for something (*guess*: physics-enabled contest world). `World.SimTime()` is the sim clock, `World.Pause/Resume`. Recording: `World.OpenVisualOutput(file.bmv)` + `World.WriteVisual()` per tick; playback via `OpenVisualInput/ReadVisual/SeekVisual`.
- **Segments:** named visual/UI objects (`Segment.<name>`, `Segment.GetId`, `Segment.Destroy`). Zook roots are reachable as `Visual.GetRootPosition(group)`; the camera code uses group **1 = green Zook, 5 = red Zook** (matches `flags` "1"/"5" in `.contest` placeholders).

### C.3 Physics and time (Karma integration)
- Step settings in the exe (defaults read from its constants): **`simulation_stepsize` = 0.005 s** (200 Hz), **`max_tick_size` = 0.02 s** (`Scripts/PhysicsConstants` sets 0.02; the Motion Player sets 0.0625). Results are deterministic in sim time regardless of frame rate (*verified*), so physics uses fixed steps.
- `PhysicsConstants` also sets `Cardan_Stiffness 1000`, `Cardan_Damping 100000` (Zook joints are Cardan/universal joints), `AngularApproachSpeed 4`, `contest_unit_scale 40`, `pixel_offset 0.5`, and seeds the RNG from the clock. Commented-out knobs show what Karma exposes: `Friction`, `Restitution`, `PrimarySlip`, `SecondarySlip`, `MaxAdhesiveForce`, `LinearVelocityDamping`, `AngularVelocityDamping`, `MaxAngularVelocity`, `Softness`, `Physics_Sim_Epsilon`, `Physics_Sim_Gamma`; also `Physics_Sim_MassScale`/`LengthScale`, `auto_set_inertial_tensor`, `physically_connected_integrity`, `physics_intrusion_mass`, `max_angular_vel`.
- Unstable Zooks: config `unknown_physics_error_message` ("One of the Zooks being simulated has caused a simulation error … The application will now exit.") means a bad genome can kill a run. The harness must treat that as a result (a loss or invalid entry), not a crash.

### C.4 Zook (`Evo`) agent
- `# Evo specializes BaseTarget embeds Genome, Visual, Karma, Neural, Input, Sound`. Created by `Contest` as `Agent.Create("Evo", position, Quatn, zookFile, 0, 0, fixed)`.
- Messages (*source*): `Fix(pos)`, `Hardness(seg)`, `SegmentSpeed(seg)`, `NetSize()`, `Hit(seg)`, `Sever(seg)`, `Destroy()`, `SetTarget(data)`, `Orientation()`, `Mutate()`, `Shoot(seg, point, dir, strength)`, `StopMoving`/`StartMoving(data)`, `SmoothStop`/`SmoothStart()`, `FreezeNeural()`, `BoundingBox()`, `LowestPoint()`, **`GetPosition()`** (root position as a Vector; *verified*), `SetVisualFlags(flags)`.
- Controller vocabulary in the exe (*guess*: the Zook "brain" is a network of these node types, to confirm in M1 from the genome XML): `accumulatorfunction_{multiply,sawtooth,stepper,sum,target,timer,vector}`, `transferfunction_{bound,invert,latch,map,scale,sine,spike,velocity}`. Joint/limb parameters: `muscle_stiffness`, `muscle_damping`, `spine_amplitude`, `angle_deviance`, `x/y/z_minangle`, `x/y/z_maxangle`, `x/y/z_rotation`, `hard_limits`.

### C.5 Contests
- `ContestPacks/*.dat` define named contests: `agents` (teams), `rounds[i] = {contest = "<file>", contestants = {slot = team}}`, `camera`, `picture`. `Contests/Contest Pack/<file>.contest` define the arena: `agents` (placeholders for Zooks with `position`, `eulers`, `group`, `flags`, `movable`, `hatpin`, plus props such as `TableSumo`, `Target`, `Turntable`…) and a **`behaviours` graph** run by `BehaviourManager`.
- Behaviour entries: `id`, `name` (script in `Scripts/Behaviours`), `owner` (agent index), `params`, `triggerOnEvents` / `triggerOffEvents` / `destroyOnEvents` (lists of `{behaviour = id, id = event}`; special ids SYSTEM −2, IMMEDIATE −3, TRIGGER_ON −4, TRIGGER_OFF −5, DESTROYED −6). Behaviour scripts expose `paramaters`, `events`, `initialize`, `update(data)`, `callback`, `finalize`, and call `BroadcastEvent`, `SendMessage`, `CallManager`.
- Behaviours used per contest:

| Contest | Behaviours |
|---|---|
| CP_DODGY_ZOOK | Finish Goto ListenContact |
| CP_MERRY_GO_ZOOK | Finish Follow NoContact |
| CP_WEAKEST_ZOOK | Finish Follow ListenContact |
| CP_ZOOK_BALL_{GREEN,RED}_STRIKER | Finish Follow Freeze Goto ListenContact Loser Unfreeze Winner |
| CP_ZOOK_IN_A_CHINA_SHOP_{GREEN,RED} | Finish Follow Freeze RandomMoveNoScore Timer TotalNoContact |
| CP_ZOOK_MARBLES / CP_ZOOK_SMASH / CP_ZOOK_SUPER_HURDLES | Finish Follow ListenContact |
| CP_ZOOK_SUMO | Finish Follow Goto MonitorDistance NoContact PositionBehind Resume Suspend |
| CP_ZOOK_TAG | Finish Follow RandomMove Timer |

  All 22 behaviour scripts: Finish Follow Freeze Goto HeightCounter ListenContact Loser MonitorDistance NoContact PenaltyTimer PositionBehind RandomMove RandomMoveNoScore Resume StartMoving StopMoving Suspend Timer TotalHeightCounter TotalNoContact Unfreeze Winner.
- Flow: `Contest.Initialize` → (notify) `InstantiateContest` → `InstantiateAll` (Zooks + props + `BehaviourManager`) → `ContestInstantiated` → at sim time 4 the driver sends `Go` (Zooks `SmoothStart`, behaviours start) → reports arrive as `BehaviourReport{name, scoreboard{player, score, contestant, description}}`.

### C.6 Useful config keys
| Area | Keys |
|---|---|
| Debug | `trace_behaviours` (*verified*, very verbose), `munki_trace`, `agent_monitor`, `performance_bars`, `physics_lines`, `show_invisible`, `throw_bad_messages`, `log_buffer_size`, `safe_mode` |
| Rendering | `v_sync`, `use_dx9`, `stencil_shadows`, `shadow_quality`, `show_shadows`, `supress_textures`, `suppress_zook_textures`, `white_visuals`, `mesh_resolution`, `fogging`/`fog_start`/`fog_end`, `exclusive`, `windowed`, `resolution_x/y`, `anamorphic` |
| Camera | `camera1..4_zook` (−1 none, 0 both, 1 green, 5 red), `cameraN_target`, `cameraN_offset`, `cameraN_angle`, `video_resolution` (25) |
| Paths | `product_directory`, `default_teams_directory`, `texture_path`, `script_extension` |
| Zooks | `genome_rhesus_macaque` (password), `genome_archive_header`, `recolour_zooks`, `green_zook_*`/`red_zook_*` colours |
| Network (*unverified*) | `injection_listen_port` (default 3030), `injection_send_port`, `injection_secure`, `monitor_socket`, `performance_socket`, `socket_log`, `rmt_server`, `visual_broadcast`, `remote_visual_scale` |

### C.7 Logging and errors
- `Trace(fmt, ...)` (`%` placeholders) → OutputDebugString `[ munki ] …`. Engine channels: `[ engine release ]`, `[ engine error ]`, `[ engine karma ]`. Capture with a DBWIN listener or DebugView (the spike's listener may slow the engine; use it only for debugging).
- Script errors → `%APPDATA%\BonsaiErrorLog.txt` (file, line, function, message being handled, receiving class) **plus a modal dialog that blocks the engine**. Always run with a timeout.
