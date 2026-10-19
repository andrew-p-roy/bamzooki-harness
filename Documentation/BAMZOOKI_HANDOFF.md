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
Teams\examples\Creatures\<Name>\<ver>.zook   Ant, Leapsa, Scrabber, Spider, Twigger
NewCreature\1.0.zook
Fonts\*.met, many .png/.bmp textures
```

User saves live under the user profile (found in `%APPDATA%`/`%LOCALAPPDATA%`; old apps writing to Program Files may also land in `%LOCALAPPDATA%\VirtualStore\Program Files (x86)\BAMZOOKi`). **Confirm the exact save root** and record how the engine labels it (`%HOME%`, `MYDOCUMENTS`, `EXPORT`; see §3).

**Zook naming rule (verified in game):** the file must be called `1.0.zook` (version naming `<major>.<minor>.zook`) and the **Zook's name is its folder name**: `.../Creatures/<ZookName>/1.0.zook`.

---

## 3. Engine facts

- **Bonsai Engine** by Gameware Development ("Creature Labs"), build 158, dated 7 Apr 2006. Graphics: **RenderWare 3.4**. Crypto: **Crypto++**. Compression: **zlib**.
- The game is **almost entirely Lua scripts** run by `Bonsai.exe`. The Lua dialect is old (Lua 4/early 5 style): `for i,v in t do`, string functions under `String.strfind` / `String.strsub`, and `Number.random`. Check the exact version before writing scripts (e.g. look for `_VERSION`, or try `pairs`).
- **Launch command lines** (from the installed shortcuts):
  ```
  Bonsai.exe -init builder           -videocfg builder.vcfg           -splash ".\BAMZOOKi Zook Kit Splash"
  Bonsai.exe -init contestrunner     -videocfg contestrunner.vcfg     -splash ".\BAMZOOKi Simulator Splash"
  Bonsai.exe -init contestfileplayer -videocfg contestfileplayer.vcfg -splash ".\BAMZOOKi Motion Player Splash"
  ```
  `-init <name>` selects `Scripts/Boot/<name>.ssx`, so **a custom boot script = `-init <ourname>`**.
- Other command-line flags found in the exe (*unverified behaviour*): `-debug`, `-luastack`, `-safe` (sets `safe_mode`), `-monitor`, `-studio`, `-splash`, `-videocfg`. The Motion Player boot script also parses `-f <file.bvz>` from `System.GetCommandLine()`.
- **Path labels** in the exe: `%ROOT%`, `%SCRIPTS%` = `%ROOT%/Scripts/` (boot scripts in `Boot/`), `%ALLAGENTS%` = `%ROOT%/Agents/`, `%WORLDAGENTS%`, `%GOVERNORAGENTS%`, `GENOMES` = `%ROOT%/Agents/World/Evo/`, `%HOME%` (`%HOME%\Worlds\`), `MYDOCUMENTS`, `EXPORT`.
- A config variable **`script_extension`** exists in the exe. It may allow loading plain `.lua` scripts instead of encrypted `.ssx`/`.sax` (*unverified*). If not, re-encrypt (§4.3; round-trip verified offline).
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

- `.zook` user header = `"\nThis zook file is not compatible with this software. Please upgrade your software."`. It is a decoy message for older versions, set by config `genome_archive_header`.
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
- **Re-encryption** (for patched or custom scripts): zlib-compress the source, split it into 22-byte chunks, OAEP-encrypt each with the public key derived from `he.skx`. Round trip verified offline; not yet tested in game.

### 4.4 Other formats

- `ContestPacks/*.dat`, `default.dat`, `zook_list.tab`, `TeamSelection.dat`: plain-text tables (`"key" = value`), read and written by `System.ReadTable` / `WriteTable`.
- **Recordings:** a contest is recorded with `World.WriteVisual()` on a timer into `TMP_VIDEO_PATH/TempSaveVideo.bmv`, then `System.Zip(..., file.bvz, 6)`. `.bvz` is the zipped `.bmv`. The Motion Player unzips and replays it. The `.bmv` format itself is undocumented and is a candidate for later decoding to render outside the engine.
- `.dff`: RenderWare meshes. `.bmi`: passport images, a 256×256 RGB hex string in an archive table (`"image" = "080808 0001 0001 <rgb hex>"`).

---

## 5. Secrets handling (repo must stay clean)

There are two secrets, and **both must be derived at runtime from the user's own install, never committed**:

1. **Script key:** load `<install>/he.skx` directly.
2. **Zook password:** decrypt `<install>/Scripts/GenomeConfiguration.ssx` with (1), then extract the string argument of `Config.Set("genome_rhesus_macaque", "...")` with a regex.
   - Similarly, `registry_key` (protects `Registry.sdx`) is set in `Scripts/Boot/Builder.ssx`. It's only needed if the harness has to read or write the registry settings file.

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
| `Agents/Governor/ContestRunnerContest/ContestRunnerContest.sax` | `MessageBeginContest`, `BeginRound`, `MessageBehaviourReport` (scoreboard: `message.scoreboard.score[1..2]`, scored as `contestDef.score` = green/red/both), `MessageEndRound` (iterates `contestDef.rounds`, then `EndContest` → `ContestFinished`), recording (`StartRecordTimer` / `TimerRecord` / `RecordThenGo`) |
| `Agents/World/Contest/Contest.sax` | `MessageContestantsSelected`, `InstantiateAll`, `Go`, `BehaviourReport` (builds scoreboard with contestant ids and descriptions) |
| `Scripts/Behaviours/*.ssx` | Win/lose and scoring rules per contest |
| `Agents/World/Evo/Evo.sax` | Evolution-related world agent (`Initialize(pos, rot, genomeFile, ...)`, damage/collision). Investigate: combined with `Genome.Mutate` there may be a built-in evolution path. |
| `Agents/Governor/Photographer/*.sax` | Renders Zook snapshots (`Camera.GetImageAsString`), useful for vision feedback |
| `Scripts/AccessCodedTableKey.ssx` | Encrypted table read/write helper (`Registry.sdx`) |

---

## 7. Objectives & milestones

### M0: Setup & verification
- [ ] Config: install path, save-root path, and Wine prefix (if not Windows).
- [ ] `tools/extract.py`: derive keys (§5), decrypt all scripts into the gitignored cache, decode the example Zooks.
- [ ] Determine the Lua version and whether `script_extension` allows plain-text scripts. Otherwise confirm re-encrypted scripts load (start with a trivial `-init hello` boot script that calls `Trace` and exits).
- [ ] Find where `Trace` output goes (log file? `-debug` console?). This is the main debugging channel.
- [ ] Confirm the engine runs under Wine (Linux), then under Docker + Xvfb.

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
- Whether contest scoring is deterministic (same Zooks give the same result?). This matters for evaluation, so test it early.
- How `Trace` / logging output is exposed.
- Whether the engine can run without a visible window or with rendering disabled.
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
