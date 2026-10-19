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
- **Do not give the engine a pipe for stdout/stderr.** With `stdout=PIPE` the main loop stalls after about 1 s, even though nothing is ever written. Use DEVNULL or a file. (This first looked like a "window must have focus" problem. It isn't: with stdout redirected away from a pipe, the engine runs fine in the background.)
- **Speed:** with rendering on, contest sim time runs at about real time (30.6 s sim in 33.8 s wall clock). Hello-world boot and exit takes about 3–4 s.
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
- [ ] Confirm the engine runs under Wine (Linux), then under Docker + Xvfb. *(Not attempted.)*

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

### M2: Headless contest runner (script the real engine)
- [ ] Custom boot script `aiharness` (launched with `-init aiharness`), modelled on `ContestRunner.ssx`:
  - Read a **job file** (`System.ReadTable`) with: contest name, two Zook paths, number of rounds, record flag, render flag, output path.
  - Load contest definitions as `ReadContestDefinitions` does, then drive `ContestRunnerContest` / `Contest` with `BeginContest` directly, with no UI.
  - Hook `BehaviourReport` and the round/contest end to collect scores per round.
  - Write a **result file** (`System.WriteTable`; convert to JSON on the Python side), then `System.ExitMainLoop()`.
- [ ] Python `run_contest(contest, zook_a, zook_b, record=False) -> Result` that writes the job, launches the engine (Windows or Wine), waits with a timeout, and parses the result.
- [ ] Investigate faster-than-real-time simulation or disabling rendering for batch runs (world time step, `World.SimTime`).
- [ ] Parallelism: one engine process per Wine prefix or container.

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
- Whether contest scoring is deterministic. Two Sumo runs gave identical scoreboards, but those ended at t≈4 s, so this is not yet meaningful. Test it on full-length bouts.
- **Contest win conditions are unreliable as shipped.** Nothing in the consumer Simulator acts on `finish` (§6), and at least Sumo's `NoContact` (`timeout = 0`) fires immediately. For M2, decide per contest whether to (a) patch behaviour params at load time (e.g. a non-zero `NoContact` timeout), (b) ignore `finish` and judge from world state at a fixed sim time (positions, contacts), or (c) both. Survey all 12 contests' behaviour graphs first.
- Whether the engine can run without a visible window or with rendering disabled. A window is still created (SimpleFrame). It doesn't need focus, but runs at about real time.
- Capturing frames: `PrintWindow` on the engine window gives a blank (blue) 3D view, so video will need the in-engine path (`Camera.SaveAsJpeg` inside an agent embedding Camera) or a desktop-duplication grab.
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
