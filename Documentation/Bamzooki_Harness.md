# BAMZOOKi AI Harness: Findings and Reference

This is the reference for the harness: what the engine and file formats are, how each part was built, and what we learned running real models through it. Everything was verified against a real install (Zook Kit v3.1, Bonsai Engine build 158) unless marked *unverified*, *source* (read in scripts or the exe) or *guess*. For setup and day-to-day commands, see the [README](../README.md).

**Status (October 2026):** M0–M4 work, plus the arena:
- codecs and runtime key derivation;
- the Zook SDK;
- an on-screen contest and trial runner with verdicts, replays and MP4s;
- an MCP server with vision previews, challenges and run budgets;
- the `Model Instructions/` folder (design guide, agent prompt, manual);
- an OpenRouter **arena runner** in which any tool-calling model takes the challenge in turn while you watch and chat with it (§7, "Arena").

Model output lives in an **arena folder outside the repo**.

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

### M1: Zook SDK (Python) — implemented (`bamzooki.sdk`)
- [x] Codec: archive container read/write (§4.1), key passed in (`codec.archive`).
- [x] Genome documents (`sdk.genome`): an exact parser/serializer. All 7 install Zooks round-trip **byte-identically** (including the CRLF v1 template). Format: one element per line pair, `\t` indent, `<tag k = "v" … >` / `</tag> `, attributes in file (= ASCII) order, no text content.
- [x] Design schema (`sdk.schema`): every Zook Kit slider with its genome attribute, range and default, taken from `BuilderParts`' own slider definitions (`myAttributeGroups` / `myRequiredAttributes`), grouped like the Zook Kit panels (size, shape, attach, rotate, movement, gait (root only), look).
- [x] Spec ⇄ genome (`sdk.zook.Zook`): `to_spec()` folds mirror twins (siblings sharing `mirror_group` with opposite `phi` and the same sub-structure) into one part with `mirror: true` (plus `mirror_phase` when the twin's `leg_phase` differs). `from_spec()` builds a v2 genome: twins get `phi`/`yaw`/`roll` negated and `ik_side` Left↔Right swapped, and share mirror groups with the original subtree, matching the Builder's `CloneInMirrorGroup`. Legacy attributes (`spine_amplitude`, `side`, …) round-trip in `legacy`.
- [x] Validation (`validate_spec`): ranges, enums, unknown keys, name ≤ 25 characters (Zook Kit limit), mirror needs `around ≠ 0`, and the Zook Kit's IK rules (`CanBeIK1`/`CanBeIK2`: *double* needs a non-root, non-moving parent; only one double per parent; no double under single/double). Edit ops (`apply_edits`): set/add/remove by path.
- [x] **Finalising in the engine** (`Engine.workshop`, Lua `HarnessBuilder specializes BuilderParts`). A draft is loaded by the game's own Builder code, which:
  - clamps out-of-range sliders and reports the clamping;
  - assigns mirror groups;
  - lays every part out on its parent's surface (`posx/posy/posz`, ray-cast against the parent mesh). **`Evo` positions parts from these stored values, so drafts must be finalised before a contest;**
  - creates default IK paths for moving parts (`MessageIKPositions`; `Evo` ignores movement without IK data);
  - computes passport stats (`SetPhysicalProperties`: height/length/width ×4 cm, weight, components) and saves an archived v2 Zook.

  `HarnessWorkshop` then photographs it (`HarnessCamera.Snapshot` → PNG) from named views. The passport photo album is simply omitted, which the Builder supports (`RemovePassportImage`).
- [x] Store (`sdk.store.Zoo`): `<arena_dir>/Zook Entries/<entrant>/<Name>/<n>.0.zook` + `.json` + previews (§7, "Arena folder"). Refs: `Ant`, `Name`, `Name@n`, `entrant/Name@n`.
- [ ] Optional: export to the game's `Teams/<team>/Creatures/...` (+ `zook_list.tab`) so designs can be opened in the Zook Kit.

Findings while building it (*verified*):
- **Only Blob meshes work in contests.** The Zook Kit offers Cube and Sphere, and they render in the Builder, but a Zook containing either makes `Evo` fail ("Unknown error in function Initialize"). All 154 parts in the example Zooks are Blobs, so the schema allows Blob only.
- The Zook Kit's default texture `bare` has no file. Missing textures are tolerated, though: two examples reference `Test` and `white.bmp`, which don't exist. The harness defaults to `aawhite` and only warns about unknown names. Texture names may include `.bmp`; `NewTeam/Textures` and `Teams/examples/Textures` together give 56 names.
- Root gait sliders (`zook_speed`, `turn_sharpness`, `turn_smoothness`) take precedence over the stored legacy fields `IKspeed`/`minAmplitude`/`minAngle`, which `Evo.AddRootBits` derives from them. `Evo` builds the Zook's neural controller itself from the body; there is no brain to design.
- Directions: the Zook faces +z; `attach.around` 0 = front, ±90 = sides, 180 = back; negative `elevation` = upper half; positive `rotate.up_down` tilts a part downwards. A Blob of size 1 is a unit-sized ellipsoid.
- `BuilderParts` has a built-in `MessageMutate` (Gaussian slider mutation), which is a candidate for M5 search.

### M2: Contest runner (script the real engine, on screen) — implemented
`bamzooki.runner` (`run_contest`, `Engine`, `Job`, `Result`), CLI `tools/run_contest.py`. How it works:
- **Sandbox:** the engine runs from a copy of the install (`.cache/bamzooki/sandbox/install`); the install is never touched. `Engine.prepare()` encrypts exactly two of our files into the sandbox: the loader (`Scripts/Boot/aiharness.ssx`) and the world-agent class `HarnessCamera` (`Agents/World/HarnessCamera/HarnessCamera.sax`). Everything else is plain-text Lua in `src/bamzooki/runner/lua/`, read at runtime.
- **Job/result files** are Bonsai text tables (`bamzooki.codec.table`). A run writes to `.cache/bamzooki/runs/<timestamp>-<contest>/`: `job.dat`, `boot_log.txt`, `driver_log.txt`, `result.dat`, optional `replay.bvz` and `contest.mp4`.
- **Driver** (`AIHarnessDriver`): reads the `.contest` file, applies behaviour fixes, **pre-assigns Zooks by arena colour** (green → placeholder `flags "1"`, red → `flags "5"`; `Contest` accepts pre-filled `params.contestant` and an empty selection list), creates the world, cameras and lights, sends `Go` at sim 4 s, and records every `BehaviourReport`, a position track (0.5 s) and the final positions. The pack's `round.contestants` mapping is ignored on purpose, so "green" and "red" are always the arena's own colours.
- **Window/focus:** the engine's own window at its default size (900×700, client ≈ 884×662) and position. The runner focuses it at launch, takes focus back after the user has been idle for 3 s (`focus="idle"`; also `"launch"`, `"always"`), and kills the engine if an error dialog appears (title contains "Error"), returning the `BonsaiErrorLog.txt` text. `--window WxH` / `--position right|left|center` exist but are opt-in (resizing after launch looked jumpy). The `.vcfg` size is **ignored** in windowed mode.
- **Judge** (`runner.contests.judge`): winner by colour from the first `finish` scoreboard (contestant ids → colours). A one-sided scoreboard (Zookball: only the striker is scored, 1 = goal, 0 = ball out) gives the other side the complement. If `finish` has no scoreboard (China Shop), the latest `score` reports are used. Also reports `fell` per colour (dropped more than 5 units below the start).
- **Behaviour fixes** (in memory only): `NoContact.timeout` 0 → 0.5 s for all contests (Sumo and Merry-Go-Zook ended instantly otherwise), and a per-contest patch for **Weakest Zook** (the green pit sensor `ListenContact` #5 has no target; set it to the green placeholder, agent 3).

Survey: all 12 contests with Ant (green) vs Spider (red), seed 1 (`tools/spike/all_contests.py`; times are sim seconds after Go):

| Contest | Winner | Decided by | Finish | Notes |
|---|---|---|---|---|
| Dodgy Zook | red | finish | 19.4 | race through obstacles |
| Merry Go Zook | green | finish | 16.3 | knock-out on a turntable (needs the NoContact fix) |
| Weakest Zook | red | finish | 8.2 | green fell into the pit (needs the patch) |
| Zookball Green To Score | red | finish | 11.3 | striker failed (one-sided scoreboard) |
| Zookball Red To Score | green | finish | 7.6 | striker failed |
| Zook Marbles | red | finish | 13.5 | race |
| Zook Smash | red | finish | 43.1 | race |
| Zook Sumo | red | finish | 3.0 | knock-out (needs the NoContact fix; both Zooks then fall off) |
| Zook Hurdles | red | finish | 14.6 | race |
| CP_ZOOK_IN_A_CHINA_SHOP_GREEN / _RED (not in pack, 1 player) | — | score | 30.0 | items knocked down: Ant 36, Spider 44 (whether more is better is unconfirmed) |
| CP_ZOOK_TAG (not in pack) | red | finish | 45.0 | 9 vs 2 |

Remaining:
- [ ] China Shop objective (maximise or minimise the knock-down count?) and whether Tag/China Shop should be offered to models.
- [ ] Repeatability across contests (Sumo was verified identical with a fixed seed; the rest are untested).
- [ ] A "draw" policy for timeouts (e.g. race progress from the track) if models need a result every time.
- [ ] One engine at a time per desktop, since it needs the foreground. Parallelism (Wine/Xvfb or VMs) is optional later.

### M3: Rendering (live + video) — implemented
- [x] **Live:** default is the game's own 5-view SplitScreen with lights. `--camera single` uses one full-window `HarnessCamera` with a named view: `follow` (both Zooks, offset (18, 20, 65), minimum angle 30°), `overview` (the game's whole-arena camera), `green`/`red` (chase cams). The camera re-fits to the window's drawable area every frame.
- [x] **Recording:** `--record` writes `replay.bmv` via `World.OpenVisualOutput` + `World.WriteVisual` (every 0.02 s, from sim 1 s) and zips it to `replay.bvz` (`System.Zip`). `tools/replay.py play <run>` opens it in the game's Motion Player (`-init contestfileplayer -f <file>`, which must be the last argument). Verified: it loads; press play in its UI.
- [x] **Video:** `--video` captures frames in-engine and encodes an H.264 MP4 with ffmpeg into `<arena_dir>/Renders/<entrant>/<YYYY-MM-DD_HH-MM-SS>_<contest>_<green>-vs-<red>_seed<N>.mp4` (`render_dir` in config overrides it). `<entrant>` is the folder form of the model operating the run (`--model` / `model=` / `$BAMZOOKI_MODEL`, default `manual`), and a `.json` sidecar records the verdict, Zooks, seed and run folder. `tools/replay.py render <run> [--view …]` re-renders a `.bvz` to `replay.mp4` with any view, using a frame-stepped player (`HarnessPlayer`).
  - Capture uses `Camera.GetImageAsString()`. `Camera.SaveAsJpeg` (called by the game's dead code) **doesn't exist** in this build, and `Camera.SaveToFile` exists but **writes nothing**. The string is hex: `08080800` + width (4 hex) + height (4 hex) + RGB24. `video.py` decodes it and streams raw frames into ffmpeg, then deletes the `.hex` files (about 1.4 MB per 640×360 frame).
  - Frames are indexed by **clock time** (sim time live, replay time in the player), and gaps are filled with the previous frame, so video timing is exact even when the engine runs slower than real time.
  - Cost: capture slows the engine in proportion to pixel count. Measured: 640×360 ×0.73 real time, the default ~884×662 window ≈ ×0.85, 1280×720 visibly not smooth on screen. Without capture, even 1920×1080 runs at about real time. Rendering itself is cheap.
  - Replay rendering: `World.ReadVisual(speed)` advances **speed × 0.02 s of replay time per call** (frame-stepped, not wall-clock), so it's deterministic. With the 0.07 s update timer, speed 2 gives one 25 fps frame per update (~0.5× real time).
- [ ] Optional: ffmpeg `gdigrab` screen capture (real-time, any resolution) if in-engine capture is too slow for high-resolution video.
- [ ] Optional: picture-in-picture or side-by-side layouts (e.g. engine view plus the model's reasoning in a terminal) can be composed in ffmpeg from `contest.mp4` and a screen recording.

### M4: Model-facing API — implemented (`bamzooki.studio`, `bamzooki.mcp_server`)
- [x] `Studio(model)`: the design guide (schema + textures + tips), contests with plain-language rules (`runner.contests.RULES`, our wording from each behaviour graph), Zook listing, `get_zook` (spec + stats), `validate`, `save_zook` (validate → draft → finalise in the engine → previews → store), `edit_zook` (edit ops → new version), `preview`, `run` (verdict, winning Zook, scores, finish time, falls, distance travelled, MP4), and `results` (`zooks/<model>/results.jsonl`). Engine jobs are serialised with a lock.
- [x] MCP server (`python -m bamzooki.mcp_server --model <name>`, stdio; MCP Python SDK 2.x `MCPServer`). Tools: `get_design_guide`, `list_contests`, `get_contest`, `list_zooks`, `get_zook`, `validate_zook`, `save_zook`, `edit_zook`, `preview_zook`, `run_contest`, `list_results`. `save_zook`/`edit_zook`/`preview_zook` return preview images as MCP image content (vision feedback). Verified over stdio with the MCP client.
- [x] End-to-end check: a quadruped designed from scratch as a spec (blue spotted body, mirrored alternating legs) was finalised, previewed, and ran 40 s of Sumo against Ant (it walked 34.5 units, neither Zook fell, no winner by the time limit).
- [x] **Trials** (`Engine.trial`, `run_trial`): the Zook Kit's own Test-mode trials driven without its UI (Lua `HarnessTrial`). It uses the same plumbing as the Builder:
  1. a `ClickableTarget` registered as Config `BuilderTarget`;
  2. the trial environment (`SprintEnv`, `Hurdles`, `Lap`, `BlockPush`, `HighJump`) created with `(nil, 130, 130, Quatn)`;
  3. `BuilderParts.Express` at the environment's `StartPosition`;
  4. `ReportEvent(env, creature, "StopWatch")` right after expressing (HighJump measures from creation) and again at Go;
  5. the environment calls back `StopWatch` and `AddDetail("Achievement", "Trial: …", value, unit)`.

  Results match the Zook Kit's stored passport values (Spider sprint 54.9 = 54.9 cm/s, Twigger 10.3 vs 10.4, Spider lap 18.2 = 18.2 s). Free play in any `*Env` (`free:SlopeEnv`, …) is also available. Trials return forward speed, drift, height range and falls, plus stills.
- [x] **Stills for vision models:** contests and trials with video return a 2×2 contact sheet (contest: Go, halfway, the finish, the end).
- [x] **Design guide for models** (`Model Instructions/Zook Design Guide.md`, served by `get_design_guide`; see "Model Instructions" below): units and weight, attachment geometry, joints and muscles, the stride cycle, single and double stride paths (`ik_points`: semantics read from `Evo.UpdateOneLink`/`UpdateTwoLink`), steering (`turn_sharpness` → minimum amplitude 1 − 2s, `turn_smoothness` → angle 180·s), targeting, body wave (`movement.wave` = legacy `spine_amplitude`, now exposed), gait recipes, what wins, and the test loop. The stride-direction advice was verified (a hexapod walked backwards, then forwards with the path's x flipped).
- [x] **Challenges** (`bamzooki.challenge`, `challenges/default.toml`): task text, test-run budget (default 20 runs; each `run_trial`/`run_contest` is one game the model chooses via arguments: contest, its Zook, the opponent (example or own version), seed; failed engine runs don't count), submissions (default 1), and ONE evaluation game with draw points. The evaluation game is a contest, an opponent, a seed and a side; the default is Zook Sumo vs Spider, green, **seed 7, kept secret**.
  - Observed: a Spider copy as green loses to Spider as red at seed 1, so side and seed matter.
  - Every run result carries `budget` (used, left, reminder).
  - MCP tools: `get_challenge` / `submit_design`.
  - Per-entrant state is kept in `<arena_dir>/Zook Entries/<entrant>/challenge-<name>.json`; `tools/challenge.py status|reset`.
  - `challenges/short.toml` is the same task with 3 runs, for quick sessions.
  - **Time limit:** `[scoring] time_limit` (default 120 s of play after Go) applies to every contest in a challenge, test games and the evaluation alike. Models can't change it: `run_contest` no longer takes a `duration`. A game with no winner by then is a draw (`draw` points, 0.5 by default). Sumo has no built-in clock (it only ends on a fall), so this limit decides stalemates. Trials keep the Zook Kit's own limits (sprint 20 s, lap 60 s, …). The wall-clock time is longer when the engine window loses focus, because the game slows down rather than skipping time.
- [x] **Hidden evaluation seed** (`reveal_seed = false`, the default in both shipped challenges). Results are deterministic for a seed (§3), so a model that knows the evaluation seed can rehearse the exact evaluation game with its test runs and submit whatever won it. The first test session did exactly that (see "Model test sessions"). `get_challenge` now shows the seed as "secret (the same for every entrant)": still fair between entrants, but models must design for robustness and pick their own test seeds.
- [x] **Opponent isolation:** a model sees and can reference only the example Zooks and its own designs. Other entrants' designs are hidden from `list_zooks` and rejected by every tool. (Evaluation opponents in the challenge file may name anything, since the operator controls that file.) For this to be watertight the model must not have file access to the arena folder, which is why the arena runner gives it nothing but the MCP tools.
- [x] **Entrant names** (`sdk.store.entrant_folder`): an entrant is identified by its **full model name**, such as `anthropic/claude-sonnet-4.5`. Its folder is that name made filesystem-safe (`/`, `:`, … → `_`, e.g. `anthropic_claude-sonnet-4.5`; 1–120 characters; `examples` is reserved).
  - The name comes from `--model` (or `$BAMZOOKI_MODEL`).
  - Without one, the server starts unregistered, and the model calls `register_entrant("<its full model name>")` once. Only names not yet used are accepted, so a model can't adopt another entrant's folder.
  - The arena runner always passes `--model` and hides `register_entrant`.
  - Example Zooks can be read before registering.
- [x] **The manual as a tool** (`bamzooki.manual`, `read_manual`): the official Zook Kit PDF has a usable text layer. `pypdf` extracts it, and `manual.clean()` fixes its quirks (headings come out with each letter tripled, `MMMaaakkkiiinnnggg` → `Making`; some paragraphs appear twice). `read_manual()` gives a page index, `pages="17-26"` gives page text, and `search="steer"` finds pages.
  - The PDF is BBC material, so it isn't in the repo. The user copies it to `Model Instructions/BAMZOOKi Manual.pdf` (gitignored), and the asset guard now rejects any `.pdf`.
  - Reading it corrected the design guide: **targeting Normal = away from the target, Inverted = towards it** (the guide had this backwards). It also contributed a table mapping each Zook Kit panel option to its spec field, and confirmed movement tips (feet must reach below the body, and IK point density sets the speed along the path).
- [x] Local HTTP endpoint: `python -m bamzooki.mcp_server --model X --http` (streamable-http, `127.0.0.1:8765/mcp`), verified with the MCP HTTP client.
- MCP tools (16): `register_entrant`, `get_challenge`, `submit_design`, `get_design_guide`, `read_manual`, `list_contests`, `get_contest`, `list_zooks`, `get_zook`, `validate_zook`, `save_zook`, `edit_zook`, `preview_zook`, `run_contest`, `run_trial`, `list_results`. Server flags: `--model`, `--challenge`, `--no-challenge`, `--http [--host --port]`.

### Model Instructions (`Model Instructions/`, top level)
Everything a model is told lives in one folder that's easy to find and edit. Nothing in code needs to change to reword it.

| File | Use |
|---|---|
| `Agent Prompt.md` | System prompt for an entrant. Rules: use only the bamzooki tools; never open, read or list files, including paths that appear in tool results; register with the full model name if asked. Also covers how to work (challenge → guide → study examples → free previews → plan each run → submit) and the **presentation** to the audience at the end (design, testing strategy game by game, result, what next). The arena runner sends it as the system prompt. For Claude Code/Desktop, paste it as the first message. |
| `Zook Design Guide.md` | The design guide (`get_design_guide`), including §9 "Testing strategy: you choose every game" |
| `BAMZOOKi Manual.pdf` | Your own copy of the manual (gitignored), served by `read_manual` |
| `README.md` | What's here |

### Arena: models compete through OpenRouter (`bamzooki.arena`, `tools/arena.py`)
A command-line show runner. Each challenger, given as an OpenRouter model id, takes the challenge in turn. You watch its reasoning in the terminal and its games on screen, then talk to it.

**Arena folder.** Everything models produce lives **outside the repo**, in `arena_dir` (default `~/BAMZOOKi Arena`, env `BAMZOOKI_ARENA_DIR`):
```
<arena_dir>/Zook Entries/<entrant>/<Name>/<n>.0.zook (+ .json, previews)   designs
<arena_dir>/Zook Entries/<entrant>/challenge-<name>.json, results.jsonl     budget, submissions, results
<arena_dir>/Renders/<entrant>/<timestamp>_....mp4 (+ _stills.png, .json)    videos and contact sheets
<arena_dir>/Transcripts/<entrant>/<timestamp>.md                            full conversations
```
`zoo_dir` and `render_dir` can still override the first two individually. The repo's old `zooks/` and `renders/` folders are no longer used.

**How a challenger runs.**
1. Query OpenRouter `/models` for the id. Skip models that don't list `tools` in `supported_parameters`. Vision support is read from `architecture.input_modalities`.
2. Start the MCP server over stdio as a child process: `--model <id> --challenge <file>`, **working directory = the arena folder**, with the `BAMZOOKI_*` environment passed on explicitly. `stdio_client` otherwise passes only a minimal environment; found in the smoke test.
3. Offer the model the MCP tools as OpenAI-style function tools, except `register_entrant`.
4. System prompt = `Agent Prompt.md` plus "you are registered as `<id>`". Kick-off message: "Begin the challenge now…".
5. Agent loop over `/chat/completions`:
   - Tool results go back as `tool` messages.
   - Images (previews, contact sheets) go back as a follow-up user message with `image_url` data URIs. This only happens for vision models, and only the latest 2 image messages are kept to save tokens.
   - Each tool call prints a one-line summary (refs, verdicts, the budget reminder).
   - If the model stops without submitting, it's nudged (up to 3 times).
   - `--max-turns` (120) caps a session.
   - Cost and tokens are tracked from `usage: {include: true}`.
6. When the model answers without tool calls (normally its presentation), you get a `you>` prompt.

**Commands.**

| Command | Effect |
|---|---|
| `/next` | Call the next challenger in the queue |
| `/challenger <id>` | Call a specific model now |
| `/leaderboard` | Best submission per entrant in this challenge |
| `/status` | Current budget |
| `/open` | Open the latest video and stills |
| `/list` | Show the queue |
| `/help` | List the commands |
| `/quit` | Leave |
| Any other text | Goes to the current model as an audience message. It can answer, and use its tools (spending runs if it has any left). |

`--auto` runs the whole queue unattended, then prints the leaderboard. The queue and limits come from `--challenger` (repeatable), `--challenge`, `--max-turns`, `--max-tokens` and `--temperature`, or from the `[arena]` table in `bamzooki.toml`. The API key is `OPENROUTER_API_KEY` (environment or the repo's gitignored `.env`); it's never printed or logged.

**Repo isolation.** The model gets no file, shell or web tools: only the MCP tools. The server runs in the arena folder, and every path it accepts is a Zook reference, never a file path. Result paths shown to the model (videos) are informational only.

**The show (`bamzooki.announcer`, `bamzooki.showtime`, `bamzooki.voice`).** Everything around the games that makes a session watchable. It is all optional and none of it touches scoring.
- **`--showtime` gates every extra model call** (`[arena] showtime = true`; off by default, to save tokens and time):
  - the announcer (and so `--speak`);
  - the models seeing each other's Zooks and their pre-match lines (`Arena.trash_talk`);
  - the post-match interviews and the end-of-game stills they use (`Arena.post_match`);
  - Hall of Fame records in fact sheets and standings;
  - the champion's victory speech.

  Without it the champion still learns the result (added to its conversation, no call) and takes the audience's questions. Talking to models (`/<name> <message>`, or plain text to both) works either way. So does everything that costs no tokens: the tale of the tape on screen, the fact card, `/pick`, `/exhibition`, the highlights reel with music, and the Hall of Fame prompt and `/halloffame`.
- **Announcer.** A separate, cheap, expressive model (default `anthropic/claude-haiku-5.5`, $0.10 / $0.50 per million tokens, sees images; `--announcer`, `--no-announcer`, `--announcer-style ringside|documentary|football|noir|<your own>`). It has no tools and keeps a 16-message memory of the evening, so it can call back to earlier moments. It speaks at five points: each challenger's introduction, each challenge game's result (from its stills), each knockout match's build-up, each knockout result plus the two interview questions, and the champion's crowning and sign-off. Its tokens and cost are a row in the `/end` table, and its lines go to `Transcripts/Announcer/<timestamp>.md`. A failed call is retried once. After three failures in a row it goes quiet, and the screen says why.
- **Facts, not invented trivia.** `showtime.model_facts` builds a fact sheet from OpenRouter's `/models` entry:
  - the release date, from the dated `canonical_slug` (e.g. `…-20261007`), falling back to `created` (the listing date);
  - its age relative to today, the knowledge cutoff, context window, price, input modalities, the Artificial Analysis indices when listed, and the description;
  - the maker and home city/country, from a `LABS` table keyed by the id prefix. Unknown labs get no country rather than a guess.

  The announcer's system prompt says to use only the facts it is given. A returning knockout champion's record (from the Hall of Fame) is added to its sheet. The same sheet gives the one-line card printed under each challenger's banner.
- **Seeing the opponent.** Before each knockout or exhibition match:
  - a tale of the tape (weight, parts, size, moving limbs, stride speed, look) is printed, from each design's saved `.json`;
  - the announcer gets both three-quarter previews side by side (ffmpeg `hstack`, temporary);
  - each contestant gets the *other* Zook's three-quarter and front previews (vision models) or its one-line card (text-only models) before it gives its pre-match line.
- **End-of-game stills.** After a match, a 2×2 sheet is cut from the deciding game's video (Go | 4 s before the end / just before the end | the end) into a temporary folder. The announcer calls the result from it, asks the winner and then the loser one interview question each, and both answer having seen the same sheet. The sheet is deleted after the interviews, and the folder when the knockout ends. Without an announcer, stock questions are used.
- **Telling the Zooks apart.** First live test: the announcer described the winner with the *loser's* colours. "Green" and "red" are arena sides, not the Zooks' colours, so the stills alone are ambiguous. Every result prompt now carries `tell_apart`, e.g. "Sumobot: grey metal body, 6 metal limbs. BulldozerSpider: tiger body, 10 red limbs." (body colour and texture, plus the commonest limb colour). With it, the calls were right every time.
- **Audience.** `/pick <name>` before a match predicts the winner. The result says whether the audience called it, the announcer and the interviewees hear it, and the knockout ends with the audience's score. `/exhibition <a> <b>` plays one match with the whole show between any two Zooks submitted this session, qualified or not, without touching the ranking.
- **Keeping score across sessions.** When a knockout ends, the arena asks `Add <model> (<Zook>) to the Hall of Fame? [y/N]`. Only `y` or `yes` appends the champion to `<arena_dir>/Hall of Fame.jsonl`, which is not archived with `Zook Entries`. Enter, anything else, or no keyboard skips it, and earlier typing is cleared first, so test sessions don't create champions. Exhibitions never record. `/halloffame` prints the list. Every game of a knockout is joined into `Renders/Tournament/<timestamp> Knockout highlights - ….mp4`, and `/open` plays it.
  - **Titled reel** (`showtime.titled_reel`): ffmpeg only, no model calls. Before each game comes a 3 s card: the round and game number, the green Zook's name (green) over its model's name, "v", then the red Zook and its model. During the game a semi-transparent bar along the top reads "<green Zook> (<model>) v <red Zook> (<model>)". The reel ends on a 4 s champion card.
  - Cards are `color` sources with `drawtext` and fades; everything is joined with the `concat` filter and re-encoded (a 2-game reel takes about 1.5 s).
  - Model names and Windows font paths never pass through ffmpeg's filter quoting. The font is copied, and each line of text written, into a temporary folder that ffmpeg runs in, and `drawtext` reads `textfile=`, with `expansion=none` so a `%` in a name stays literal.
  - The font is Arial Bold, Segoe UI or DejaVu Sans, whichever exists. Without one, the games are simply joined without titles (concat demuxer, no re-encode).
  - **Music** (`bamzooki.music`, `Music/tracks.toml`): under every game a looped track plays from its top, cut to the game's length, with a 30 ms fade-in and a 1 s fade-out at the end. The cards are silent, and the audio is concatenated alongside the video, so the two stay in sync.
    - The tracks are original Lyria 3 clips, made through OpenRouter (`google/lyria-3-clip-preview`, $0.04 a 30 s clip; audio output needs `stream: true` and arrives as one base64 MP3 in `delta.audio.data`).
    - **Loop points** come from `music.analyse`: ffmpeg decodes to 8 kHz mono, then plain Python works on a 10 ms RMS envelope.
      - The tempo comes from the autocorrelation of the onset strength, and the beat phase from where onsets line up, snapped back to the earliest beat.
      - The fade is the trailing run of 0.5 s windows more than 2 dB below the median, lasting at least 1 s.
      - The loop is whole 4-bar phrases from the first beat that end before the fade.
    - The detected tempos matched the requested ones within 1–2 BPM. Gemini's tempo guesses were up to 14 BPM off.
    - For `synth_funk` (fade from 28.0 s) the loop is 12 bars, 0–24.446 s. Gemini 3.8 Flash heard the loop three times and scored both seams 10/10 ("beat kept, no dip").
    - **Choosing a track:** the challenge's `music = "<track>"` (or `"none"`), else a track tagged with the challenge's contest, else the `default` one.
  - **Candidates** were judged two ways: ffmpeg level measurements (first and last 2 s against the middle, to catch intros and fades) and Gemini 3.8 Flash listening to each clip played twice (for seams, 80s feel, energy and vocals). Gemini was generous (seven of eight scored 9–10 for fit) and missed an 11 dB fade that the measurement caught, so the measurements decide on fades. Gemini's notes are in `Music/analysis.json`.
- **Voice.** `--speak` reads the announcer aloud on a background thread with the system's own speech engine: Windows `System.Speech` through PowerShell (no window, no focus stolen from the game), macOS `say`, Linux `espeak`. `--voice "Microsoft Hazel Desktop"` picks a voice. Hazel, a British voice, suits a BBC game.
- **Cost.** A real one-match exhibition (two Haiku-class contestants, Haiku announcing, with pictures) cost about $0.0025 in total, and three introductions about $0.0007.

### Model test sessions: findings
**Session 1: Claude Opus 4.8 via Claude Code** (entrant `claude-test-1`, `challenges/short.toml`: 3 runs, evaluation Sumo vs Spider, seed 1 at the time, green). The model was given only the MCP tools plus the agent prompt.
- It used only MCP tools (checked in its transcript) and followed the intended loop: challenge → guide → study Spider → design → preview → test → submit.
- `Bulldozer@1` lost Sumo to Spider at seed 1. It changed one thing (a slower gait), and `Bulldozer@2` won the **same** game: Spider fell, the bout finished 6.5 s after Go, and Bulldozer stayed on the table. It then submitted @2: **WIN, score 1.0, 2 of 3 runs used**.
- Finding: the run reproduced the evaluation game exactly (same contest, opponent, side and seed). Because the engine is deterministic, the model effectively saw the exam in advance. → **The evaluation seed is now secret by default** (seed 7, `reveal_seed = false`), and the guide's testing plan no longer mentions testing on the evaluation seed.
- Finding: its work went into a folder named from a short model label. → Entrants are now named by **full model name** (`--model`, or `register_entrant`), with all output in `<arena_dir>/Zook Entries/<entrant>/`. The session's data was moved from the repo's `zooks/` and `renders/` to `~/BAMZOOKi Arena`.

**Session 2: `google/gemini-2.5-flash` via the arena runner** (smoke test of `tools/arena.py --auto`, `challenges/short.toml`, secret seed).
- The OpenRouter loop worked end to end: the MCP server started in the arena folder, tools were offered as functions, preview images were returned (vision model), budget reminders were respected, and transcripts were written.
- Once it emitted a hallucinated tool name (`google:python_interpreter`), got "Unknown tool" back, and recovered by itself on the next turn.
- Design path: a four-legged "Pusher" with **double** legs.
  - Its first `block_push` trial timed out without moving: the run counted, as intended, since it was a real result.
  - It then walked backwards (−1.8 cm/s) and spun round (drift −175°). Its double-leg path moved the foot **forwards** (z 0 → 0.8) while on the ground.
  - It then tried the guide's "flip the x signs" tip, which only applies to single legs (double-leg paths keep x ≈ 0).
  - → The guide's tip now says it is for single legs; otherwise it was wrong. Deliberately, no extra coaching was added. Opus 4.8 built a walking, winning Zook from the same guide, and working out the movement paths is part of the challenge. The guide should stay correct, not become a walkthrough.
- With the secret seed it chose its own seed (2) for the Sumo test against Spider.
- Outcome: the evaluation was recorded as a **draw** (0.5), but that wasn't a real result. The operator closed the engine window mid-game, and the shutdown raised "Unknown error in function Finalize". The cost was $0.09 for 273k tokens. The session exposed the bugs below, all fixed.

Bugs found in session 2 and fixed:
- **Starved runs counted as real games.** The operator was busy in another window, and the "idle" focus policy only takes the engine window back after 3 s without input. So the engine barely ran: setup alone took 83 s.
  - The runner's wall-clock kill (then 60 + 2 × game seconds) was reported as `timeout`, the same word the driver uses for the game's own time limit.
  - So the run counted against the budget and showed "no winner". This happened to the Sumo test and probably to the first `block_push` trial (no result and no movement data).
  - Now a killed run is `stalled`, with an explanation for the model. It doesn't count and is never scored. The allowance is 120 + 3 × game seconds.
- **Failed evaluations used up the submission.** `submit_design` scored any non-win as a draw, including `engine_error`. Now only real results (`finished`, or the game's `timeout`) are recorded. Anything else returns "your submission was NOT used; call submit_design again".
- **The arena missed the submission.** Tool results that carry images also carry text labels, so the arena's one-line summaries tried to parse JSON + labels and failed. Its "submitted" flag was never set, and it nudged Gemini three times with "you haven't submitted yet". Gemini apologised and re-submitted ("Already submitted"). The summaries now parse the first (JSON) block, and nudges are logged in the transcript.
- **Environment not passed to the server:** the MCP SDK's `stdio_client` passes only a minimal environment to the server process unless `env=` is given, so `BAMZOOKI_ARENA_DIR` was lost. The server wrote into the default arena while the runner's transcript went to the configured one. The runner now passes the `BAMZOOKI_*` variables through and pins the arena folder.

General observations:
- Strong models follow the "one change per run" loop well.
- Weaker models over-trust single guide tips, so every tip must be correct and scoped. Keep the guide factual rather than prescriptive, though: the challenge measures the model's design and testing, not how much the guide gives away.
- The two models tested differently:
  - Claude went straight to the target contest and iterated on it.
  - Gemini spent two runs on `block_push` trials, which exposed its walking-direction bug but left a single run for Sumo.
  - With 3 runs, such strategy choices dominate the result; the 20-run default leaves room for both.

### M5: Competitions & search (later)
- [x] Models compete on one challenge in the arena (`/leaderboard`: best submission per entrant).
- [x] The show: announcer, opponent pictures, end-of-game stills for interviews, audience picks, exhibitions, Hall of Fame, highlights, voice (§7, "The show").
- [ ] Tournaments between models across the 12 contests, with Elo ratings, saved replays and videos. A natural next step is a challenge type where the evaluation opponent is the *other entrants'* submissions; the operator-controlled challenge file may already name them.
- [ ] Optional: parallel challengers (needs one desktop per engine, see M2), and a show layout recording the terminal and the engine window side by side.
- [ ] Hybrid search: the model proposes a body plan and an optimiser (e.g. CMA-ES) tunes the numbers, using `run_contest` as the fitness function.

---

## 8. Open questions / unknowns

- The meaning of the 5-byte marker `00 01 00 00 00` (possibly flags + version).
- ~~Whether passport `details` matter.~~ The harness lets the Builder recompute the physical stats when finalising. Hand-made owners/`uid`s and an omitted photo album load and compete fine. `modifications` (per-owner edit percentages) are optional.
- ~~Whether contest scoring is deterministic.~~ **Yes, with a fixed seed** (§3). Still to check: other contests, and Zooks with more complex neural controllers.
- **Confirmed on screen: in Sumo the example Zooks fall off the table.** Both end about 24 units below their start (y ≈ −24) within 15 s, at x ≈ 18–25. So with the default contest, a Zook that isn't pushed off loses by falling, which makes "last one on the table" effectively the win condition. Still to check: whether the tables are scaled by `contest_unit_scale = 40`, and whether the `NoContact` behaviours (timeout 0) are what triggers the fall.
- `injection_listen_port` (default **3030**), `monitor_socket`, `performance_socket`, `rmt_server` and `visual_broadcast` exist in the exe. "Injection" may mean sending Lua into a running engine over TCP, which would allow live control without restarting. *Unverified; worth a look for M2/M4.*
- ~~Contest win conditions are unreliable as shipped.~~ **Resolved for M2** by patching behaviour data in memory: `NoContact.timeout` 0 → 0.5 s, plus the Weakest Zook target fix (see M2). With these, all 12 contests produce a `finish` (or `score`) result. The shipped Simulator never acted on `finish`, which is probably why these bugs went unnoticed.
- ~~Whether the engine can run without a visible window.~~ **No** (on Windows); see the M0 follow-up table. Deprioritised.
- ~~Capturing frames.~~ **Resolved (M3):** `Camera.GetImageAsString()` in a camera agent. `PrintWindow` gives a blank view, `SplitCamera`'s `SaveAsJpeg` path is dead code, and `SaveToFile` writes nothing.
- Wine compatibility of RenderWare 3.4 / DirectX on the target machines.
- Whether the double-leg default path walks forwards or backwards: *unverified* (the guide makes no claim).
- How robust Sumo wins are across seeds: Bulldozer@2's win was at the seed it tested on. With the evaluation seed now secret, the next sessions will show how much a single test seed overfits.
- Cost per challenger on OpenRouter depends heavily on images: previews and stills are sent only to vision models, and only the latest two image messages are kept in context.

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

### C.6b Scripting gotchas found while building the runner (*verified*)
- **Runtime classes are governor-only.** `Agent.CreateClassFromString` works for governor agents, but world agents created inside a world can't use runtime classes ("Failed find agent class"). `specializes` can't name a world class that isn't loaded yet either ("Parent of name SplitCamera has not been found"). World-agent classes must be files under `Agents/World/<Name>/<Name>.sax` (encrypted), which the runner writes into its sandbox.
- **Messages need every declared argument.** Calling `Message<Name>(a, b, c, d)` with fewer arguments is silently rejected: `SendMessage` returns nil and no error is shown. Passing `nil` for the missing ones is not enough; always send real values.
- **Timers can't be shorter than `max_tick_size`** ("Timer delay must be greater than 0.0625" with the Motion Player's settings). The Simulator profile uses 0.02.
- `World.OpenVisualInput(file)` returns `(update_rate, start_time)` (the second value is the replay's first timestamp, not its length). `World.ReadVisual(speed)` advances `speed × update_rate` of replay time per call and returns the new time, or a negative value at the end. `World.Create(2)` is used for playback worlds.
- Contest placeholders can be pre-filled: set `agents[i].params.contestant = {fullname = "<path>.zook"}` and create `Contest` with an empty selection table `{}`. Contestant ids follow ascending agent index.
- Camera component members (from a probe): `GetImageAsString GetHeading GetOrientation SetPosition GetPosition SetOrientation Show SetBackground SetBluescreen ToFront Resize SetTransform Pick SetViewAngles Create SaveToFile ClearLines AddLine GetRay SetFog GetSize Area GetTransform GetScreenPoint SetFocalLength SetViewOffset Set SetTarget SetClipPlanes`. `Camera.Area()` returns the window's drawable size.
- Windowed mode ignores `resolution_x/y` in the `-videocfg` file; the window opens at 900×700 (client 884×662).

### C.7 Logging and errors
- `Trace(fmt, ...)` (`%` placeholders) → OutputDebugString `[ munki ] …`. Engine channels: `[ engine release ]`, `[ engine error ]`, `[ engine karma ]`. Capture with a DBWIN listener or DebugView (the spike's listener may slow the engine; use it only for debugging).
- Script errors → `%APPDATA%\BonsaiErrorLog.txt` (file, line, function, message being handled, receiving class) **plus a modal dialog that blocks the engine**. Always run with a timeout.
