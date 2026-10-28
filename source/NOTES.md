# TFT Meta Overlay

Two modes:

- **Live (default, personal use only)** — reads your screen (traits, shop, items, round) and
  adapts its advice. Riot's policy says apps "cannot make suggestions based on the player's
  current game state", so this mode is outside it: don't publish it.
- **Guide (`--guide`, policy-compliant)** — pick your build before the game; an always-on-top
  window shows that build's static guide all game: final board, carry items in priority order,
  component priority for carousels/drops, augment notes and tips. It **never reads the screen**.

## Run

```
py -m pip install -r requirements.txt
py run.py            # live overlay
py run.py --guide    # static guide (no screen reading)
```

### As an .exe (no Python needed to run it)

`build_exe.bat` builds **`dist\TFTMeta\TFTMeta.exe`** with PyInstaller (`py -m pip install pyinstaller` once).
Run it from there: inside this project it uses this project's `data` folder (your builds, history, settings),
and `TFTMeta.exe --guide` is the static guide. To use it on another PC, copy the whole `dist\TFTMeta` folder:
the first start creates `data\` next to the exe with your builds and overrides, then downloads the game
data and guides like the Python version does. It's a folder build (not one big file) so it starts fast and
antivirus is less likely to flag it.

Drag the title to move the window (saved to `config.json`, works on any monitor). Pick a build
from **▾** in the title bar. Compact size is fixed (`width` / `height` in `config.json`);
**⤢** or double-clicking the title bar fills the monitor the window is on with a larger
two-column layout (put it on a second screen). A build lock resets when a new game starts.

## Guide mode

Everything comes from `data/comps.json` + set data (`tft_overlay/guide.py`), so it is identical
from the first minute to the last — a static pre-game recommendation, which Riot allows.

- **Game plan**: style (reroll 1/2/3-cost, fast 9, level 8), roll level and timing, a realistic
  target (what a good-enough board is — 3★ is upside), when to bail, and two clickable backup
  comps picked by shared units/items. Stored per comp under `plan` (override backups with
  `pivots`).
- **Board**: units in fielding order — carries (gold) first; the dashed marker ends the level 7
  board, the rest is level 8–9 upside. Active traits below.
- **Carry items**: BIS items per carry, main carry first; alternatives shown as "alt:".
- **Component priority**: components across all BIS items, most needed first — what to take
  from carousels and drops for this build. The top one pulses.
- **Augments / notes**: from each comp's `augments` and `notes` fields. 10 comps have augment
  notes from bunnymuffins.lol/meta; add your own.

## Live mode

**How it plays (2026-10-19):**
Max mode: the board across the full width (as big as the height allows), trait icons with have/needed
down its left side; underneath, the guide's item builds (left) and pick / pivot / plan (right).
1. You pick, when you're ready: until you click a build (never automatically) the side panel shows the 3
   highest-tier builds that fit what you hold, re-ranked as your units, items and emblems change, and the
   board shows the top one. It doesn't chop and change: within a round the build shown never changes, and at
   a new round only when another of the same tier fits 15%+ better or a higher tier passes it. Until you pin,
   a shop unit only flashes BUY when it's in 2+ of the 3 builds on offer — a safe buy whichever way you go
   (pinned: your build's units).
   The board is always the build's final board (the champ guide). In stages 2-3, when TFT Academy has an
   opener for the build, one line above it says what to play meanwhile: "EARLY  Akali (3 items) · Camille ·
   Leona · Shen" (green: in your shop). Augments you take count
   too (see below). A pin is dropped when a new
   game starts, including the first game after the overlay starts, and when its build no longer exists.
   Above it, a LEVEL line ("2-1 Lv4 · 2-5 Lv5 · 3-2 Lv6 …": your level green, a step you're behind on red,
   roll steps gold) whenever the big leveling chips don't fit.
   YOUR GOLD (level / roll / interest advice) sits at the bottom of the side panel either way: the pinned
   build's plan, or the standard one until you pin (Lv 4 at 2-1, 5 at 2-5, 6 at 3-2, 7 at 3-5, 8 at 4-1).
2. Once pinned it's static: the board, the builds and the plan stay exactly as the guide has them. Live:
   shop units flash on the board, the one carousel component flashes in the builds, and the items your
   champs hold show on their hexes.
3. With a build pinned, two pivot moments, each offered once and kept up until you decide (click to switch,
   or ✕ keep my build): at 2-5 if your build's core carry hasn't shown (the alternative must already have 3+
   of your champions), and on an augment that unlocks a better build you hold 2+ champions for.
4. No stand-ins, contest pivots, synergy hints or option churn. (Notes below describe older behaviour
   where they disagree with this.)

- Polls only the round label; does a full read at the start of each round, again ~5s later,
  and on ⟳. OCR is capped at 2 threads and the process runs at below-normal priority.
- Right side (max mode) / under the items (compact) is pictures, not paragraphs: one big DO NOW
  line, PIVOT and CONTESTED banners (click to switch), OTHER OPTIONS as portrait cards (click to pin), and the PLAN as
  chips.
- The board is the main view (like teamfight.lol's team builder). In max mode it fills the left
  ~64% with traits down its side and the item panel under it; advice, alternatives and the game
  plan are on the right. In compact mode the board is on top. Champions in your shop flash on the
  board ("BUY"), and before 4-1 a late comp's 4/5-costs are dimmed ("later") so the rest reads as
  your early board.
- Re-reads as soon as your traits, item bench, augment cards (rerolls), gold or level change, at
  most every 1.5s, not just at round start. A bad read never stops the overlay: it's logged to
  data/overlay_errors.log and the next read carries on.
- Item panel (under the board) = the guide's builds exactly as listed: each carry, its 3 items and the
  two components under each. It never changes during a game. On carousel / armory the one piece worth
  grabbing flashes where it goes. What your champions hold is drawn on the board's hexes.
- Carousel / armory: the builds stay on screen; the best offered piece flashes gold inside them and the
  runner-up green (component under its item, or the item itself). Ranking: finish an item already
  started (a tank's first), give a bare tank its first item, then carries — never for carries you
  don't have or finished ones. The guide's component counts (teamfight.lol's Components box) break ties.
- The shop is read the moment it changes (5 recognition-only reads, ~0.1s), so buys flash while
  you roll. Full reads stay fast: one-line numbers use the recognition-only engine, the player
  list is cached, augments are only read on augment rounds, and scouting doesn't OCR every read.
- Traits/shop/round: OCR. Items: template matching (bench slots, icons under ally health bars,
  carousel icons). Armory choices: OCR of the card names.
- Item panel: BIS targets (✓ built, SLAM/NEXT glow); on carousel/armory the
  3 best offers for your build. Lock a build via **Build ▾** or by clicking a comp card.
- Level / roll advice: reads your level + XP, gold, win/loss streak (flame colour) and HP, and
  combines them with the comp's plan into one line — e.g. "Level now: 20g → Lv 7 (win streak 6)",
  "Keep the loss streak", "Slow roll 12g", "Stabilise: roll now". Rules are in `tft_overlay/econ.py`.
  "Stabilise: roll now" comes at 45 HP or less from 3-1 (early 3★ boards end games from ~20 HP), and after
  any round that cost you 15+ HP, however much is left.
- Augment screen: reads the three augment names and ranks them for your comp — teamfight.lol's
  best augments first, then by description: your traits/units, units of your reroll cost,
  items/artifacts/emblems (extra weight if the comp needs one), econ, combat.
- Pivot: with a build pinned, "Pivot? → X" appears when your board fits another comp much better
  or you're low on a loss streak; click it to switch.
- Opponent tracking: click a player's portrait to look at their board. The overlay notices the
  trait panel change, checks the name label over the board against the lobby list (your own name
  is learned automatically), and stores that player's traits. It guesses each scouted player's
  comp and shows "⚠ Contested: name: same comp" (or "also uses <your carry>") plus a
  "Scouted:" line. When your comp is contested and an open comp still fits your board, the pivot
  line suggests it. A full read that lands on someone else's board never overwrites your own
  state, and a board whose traits are exactly yours is yours (your label hidden by the augment screen or a
  combat), never stored as an opponent's. Cleared at the start of each game. Code: `tft_overlay/scout.py`.
- Core units and pivots: each comp's core units are its main carry, plus every 1-2 cost carry in
  reroll comps (Kayle Reroll: Kayle and Ornn). If you don't hold one by its deadline (1-2 cost:
  2-5, before the 3-2 augment; 3 cost: 3-2; 4 cost: 4-1), that comp drops in the ranking. A pinned
  comp gets "Pivot? → X" to the best comp built on units you hold. It triggers earlier (from
  2-3) when a core unit is rare in your shops, e.g. 0 Ornn in 36 one-cost slots against ~2.6
  expected, meaning someone else holds the copies. "Hold" means bought this game (shop logger)
  or explained by your trait panel. Code: `tft_overlay/roster.py`.
- No pin: the board follows the best fit for as long as you like; nothing is pinned for you.
- Ranking: the highest tier always wins among builds your board can go into (at least 2/3 as good a
  fit as the best; half as good in stage 1–2, when a few cheap units say little and reroll builds would
  otherwise crowd out every S/A build); fit only breaks ties within a tier. The tier shown is the one it
  plays at now.
- Comps are ranked by the guides' tier letters, capped by real ranked results (see SeeMeta below). A build
  that needs something — a specific augment (Consuming Flora), an emblem (in the items, an "— X Emblem"
  variant, or named in `needs`), an artifact, or its guide's own conditions (bunnymuffins' `*`: "Kha'Zix by
  2-3", "Artifacts or Emblems") — is **never suggested** until you have it (any one of a guide's conditions
  will do; Spatula / Frying Pan count for a craftable emblem; augments you took count). The Build ▾ menu says
  what each one needs, and if you pin one anyway an orange NEEDS banner says what's missing.
- Bench: with a reroll build pinned, a HOLD line above the gold line lists what to keep, most important
  first — the main carry and one other 3★ target (copies bought / 9), then the rest of its board's cheap
  units (×3 for 2★). Anything else on your bench is the first to sell; holding copies of every unit is what
  fills it. (The bench's champions aren't read — the list comes from the build and your purchases.)
- Augment screen: if an offered augment unlocks such a build, you already hold 2+ of its champions, and it
  would then beat your build, a PIVOT banner says "take X" and that augment's card says what it unlocks.
  This is the one pivot shown even with a build pinned. Hitting a 4/5-cost
  (e.g. Lux) puts builds around it first in OTHER OPTIONS ("you have Lux").
  Reroll comps decide at 2-5 whatever the carry costs.
- Board items follow your real items: ones you have are ticked and dimmed (a full unit's are all
  ticked), and the next item to build is ringed gold, so the board matches the item panel.
- Reroll comps: at the roll level the advice shows 3★ progress from your purchases for **two** units — the
  main carry and the other 3★ unit you have most copies of ("Veigar 6/9 · Alistar 3/9 — stay Lv 6"); guides
  mark up to five 3★ units, and holding copies of all of them locks your bench. The chase has a cut-off
  (1-cost carry 4-1, 2-cost 4-2, 3-cost 5-5: Azir / Rammus builds keep slow rolling through stage 5): not done by then, the advice is "Stop the 3★ chase: level up —
  sell spare copies". 1-cost rerolls stay level 4 and roll from 3-1: 55% of shop slots are 1-costs at level 4,
  45% at 5, 30% at 6 — every level floods the shop with 2- and 3-costs.
- BUY popup over the game: for each unit in your shop worth buying (your pinned build's units, else the safe
  buys), a guide-sized flashing hexagon with the champion and "BUY name" (×2 when two are up), in a row in the
  middle of your board — where you're looking while you do items and your board, so a buy isn't missed. Only the
  hexagons show, clicks go straight through (you can still drag units and scout); `"shop_popup_at": "right"` in
  config.json stacks them over the player list instead, and they're hidden during combat: from the moment TFT's "Combat" banner shows (or 30 s into the round at the
  latest; `"planning_s"` in config.json). From level 8 they only show 4- and 5-costs, and when
  you're one or two copies off a 3★ 4/5-cost (7–8 held) only that champion shows (here and on the board) so the
  gold waits for it. They sit in a row across the top of the arena (the enemy's half, empty while you plan),
  and it's hidden from screen capture so the readers never see it. A unit stops flashing (here and on the board)
  once you've bought enough: 3 copies (2★), or 9 if the build's guide has it at 3★. Needs TFT in borderless / windowed mode;
  `"shop_popup": false` in config.json turns it off.
- Copies you hold (2026-10-24): purchases alone missed carousel / orb / augment copies (8 Kog'Maw counted as 4).
  The shop card now fills that in: when buying a card completes a star-up, TFT puts stars at the left end of its
  top edge (★★ = you hold two 1★, ★★★ = two 2★ and two 1★, i.e. 8 copies; dimmed, not gone, when you can't afford
  it). Every shop read template-matches them (~0.2 ms): ★★★ → 8 held, ★★ → 2 (5 once you've bought 3+),
  purchases after that add on, and the ★★★ gone from that champion's card means the 9th landed. Copies held =
  the larger of that and your purchases (`ShopLogger.copies()`), used for the 7–8 rule, "bought enough", the HOLD
  line and 3★ progress. Checked on the one real marker in the fixtures (Teemo ★★, pc_live4) and on ★★/★★★
  pasted dim, bright and gold onto every other card; no real ★★★ screenshot yet — save a report (⚑) when you
  see one. The star badge on each unit's health bar (bronze `<|` 1★, silver diamond 2★, gold double chevron 3★)
  is easy to read, but nothing on screen says which champion a unit is, so it can't give per-champion counts.
- Your HP: a read that jumps (51 → 3 → 51) only counts once a second read agrees, so a misread can't flash
  "Stabilise".
- Augments you took: read off the little box above the lamp on the left of your board. After each augment
  screen the overlay remembers every offer it saw (rerolls too); the one that then shows up in the box is
  the one you took (colour icon match against the offers, and it must also be the icon's best match of all
  248 — so an older augment can't pass for it). They feed the ranking: a build that needs that augment
  (Consuming Flora) unlocks, artifact / emblem augments count. If the overlay starts mid-game, augments
  taken before it started aren't known. Code: `tft_overlay/augment_lamp.py`; icons from
  `py tools/fetch_augment_icons.py`.
- Contest warnings need evidence: 5+ units on their board, a confident comp fit, and your carry's
  traits on their board. No contest pivot when you already hold 3+ copies of your carry (unless 2+
  players are on it).
- ⚑ in the title bar saves the current screen + what the overlay read to tests/fixtures/reports,
  so misreads can be fixed from the real frame.
- The October 2026 client update (18.4b) moved the bottom bar (shop cards, level, XP, odds, gold, streak) 5% of
  the window to the left; for a day the overlay read about 1 shop name in 5 and wrong gold. `tft_overlay/hud.py`
  now shifts those regions and picks the layout from whichever reads more shop names, so the old layout still works.
- 📖 in the title bar opens the whole guide for your build (pinned, else the one on the board) in its own
  window: tier and ranked results, what it needs, the gameplan and every stage, tips, item holders, items,
  augments, leveling and the source link. Hidden from screen capture like the overlay; Esc closes it.
- No game running: a build you pick from the build menu still shows — its board, item builds and its guide
  (nothing from your last game; the stage advice starts at stage 1-2). Unpin and it goes back to waiting.
- Units from orbs / carousel never show as purchases. A missing-unit warning has a "✓ I have X"
  chip: one click and that unit counts as held for the rest of the game (auto mode switches back).
  If the overlay started mid-game, "not bought" isn't evidence, so missing-unit pivots stay off.
- Units you hold rank comps up: a comp your bought units already make beats one that only
  shares traits.
- Items: the main tank (the first melee carry with tank items) is itemised first, then the
  carries. Items for a carry you don't have move to a unit you hold in the same spot
  ("Sejuani (Ornn's items)"). The main carry keeps its items while you roll for it.
- Losing streaks: banking a loss streak is only advised in stage 2 above 70 HP. From 3-1, losing
  rounds means strengthening the board now.
- `data/custom_comps.json`: comps you add yourself (same format as comps.json, e.g. Defender Diana
  Reroll). Merged in at load; the importers never touch this file. Edits show within ~5 s, build menu
  included (no restart). A comp whose `needs` names an item
  you're holding (Defender Emblem) ranks up. `comp_overrides.json` also takes a `note` (your read on a comp).
- Item advice: a unit holding 3 items is finished, whatever they are (matched to its carry by the whole
  unit: on-list items + stat fit), and drops off the item panel so the next carry shows.
- `data/comp_overrides.json`: cap a comp's tier (`max_tier`) and say what it `needs` — for comps
  that only work with specific emblems/augments/items (Veigar, Solar Yunara, …). `must` adds your own
  must-have items per carry, e.g. `"Azir Rammus Reroll": {"must": {"Azir": ["Guinsoo's"]}}`. Edit freely.
- Carousel: re-read every ~2.5s while it runs; picks only show items still on the carousel.
- Demo on a saved screenshot: `py run.py --demo tests/fixtures/pc_live6.png --capturable`.

Screen pixels only: no memory reading, no handles to the game process, no input automation.

## Tocker's Trials (PvE score runs)

Tocker's is 30 rounds (1-1 to 3-10) with three lives and no streaks; income is interest plus each round's
reward.

**Scoring, from the game files** (Community Dragon `map22.bin` and `tft.stringtable`, read 2026-10-25): six
score bonuses, each popping up as "<name> +X" when awarded: **Surviving Champs, Time Bonus, Star Levels, Gold
Interest, Item Greed and Close Calls** (plus Bosses Defeated on the end screen). Score ranks start at 0, 100k,
200k, 350k, 500k, 600k (your Legendary runs) and 800k. The point values aren't in the client files (the
server sends each "+X"), so what Item Greed counts is still unknown: spare components on your bench didn't
seem to trigger it, and unopened anvils are the next thing to test. Gold Interest does score, so the advice
keeps 50 gold for interest and only spends it all on the last round, 3-10.

- **Detected from the round number:** two rounds a lobby never has (1-5 to 1-10, or any x-8 to x-10) switch the
  overlay to Tocker's for that game; the status line says "Tocker's". The round reader now reads x-10.
- **Leveling in Tocker's rounds** (`plans.tockers_plan`): every build's plan is converted by style, e.g. a 3-cost
  reroll goes Lv 5 at 2-1 (passive XP), 6 at 2-3, 7 at 2-6 and rolls there, 8 at 3-6. bunnymuffins' lobby
  leveling isn't shown.
- **Lives, not HP:** the HP number shows your lives (3 to start; rewards can add more, 4-5 seen), and the status line shows them ("Tocker's · 2 lives"). The
  lobby HP and streak rules are off. After a lost life it says to roll for upgrades, and on the last life to roll
  now. The 3★ chase never times out (nobody else takes copies). Interest scores, so it keeps the usual 50-gold
  rules and only says to spend everything on the last round, 3-10.
- **BUY popup:** cheap units still flash at level 8, because 3★s score.
- **Tocker's builds:** `"mode": "tockers"` in custom_comps.json. They're only suggested in Tocker's (or when
  pinned) but always in the menu. **Tocker's Kog'Maw Adaptor (score)** is your Kog'Maw Adaptor 4 board with six
  3★ targets (Kog'Maw, Master Yi, Krug, Vi, Yorick, Gromp) and a Tocker's stage guide, from your 612k
  Legendary run. **Tocker's Solar Kayle — Eclipse (score)** is S: your best score, 870,250 on 2026-10-28, on
  the Kayle Reroll — Eclipse guide board (Kayle, Xayah, Ornn, Leona, Sejuani, Rakan, Diana, Aphelios, Alune)
  with no lives lost. Its stage guide follows that run: Lv 6 at 2-4, 7 at 3-2, 8 at 3-6, 9 at 3-10, 55+ gold
  from 2-3.
- **METAsrc's Tocker's comps** (Set 18, 18.5, from the page you pasted on 2026-10-25), as Tocker's builds with
  their own leveling and stage guides: Kayle & Aphelios (Inferno Lunar Rapidfire Solar), Spellweaver LeBlanc,
  Azir Executioners, Solar Kayle & Xayah and Elderwood Ezreal. Items come from the closest guide builds;
  positions for units those boards lack are a guess. METAsrc's "top 4" counts runs survived, not score, so
  the letters follow your scores: **S Elderwood Ezreal (865k best, 825k, 769k, 769k on 2026-10-26)**, A Kog'Maw Adaptor (825k on
  2026-10-26 with a life lost at 2-3 or 2-4, 612k), B Azir Executioners (567k), B Spellweaver LeBlanc (515k),
  C Solar Kayle & Xayah (692k, 577k) and C Kayle & Aphelios (630k). 833k on 2026-10-27 (Legendary, now your
  third best) was a Kha'Zix + Elderwood Ezreal hybrid (Kha'Zix Reroll pinned; Elderwood 3, Executioner 3,
  Lunar 3, Ravager 2, Flora Fatalis 2). It lost a life at 1-10 and spent a lot of gold rolling for Ezreal 3★. The two Kayle builds dropped from A to C on
  2026-10-26: too weak early for Tocker's (your call), even with a Kayle 3★ reroll and early Guinsoo's. The
  Eclipse version with Alune instead of LeBlanc then set the best score (870,250, above).
- **Item Greed** (your test, 2026-10-25): 2,500 per item: 1 glove gave 2,500 and 4 items gave 10k.
  **It pays every round** (score read off the 800k run's screenshots, 2026-10-26). With items held on the bench
  the run scored about 36k per round (1-2 to 2-5); with them equipped, about 21k per round (2-5 to 3-10). One
  popup read "Item Greed +20,000" at 1-9, which is 8 items. Other popups seen: Round Won +10,000, Gold Interest
  +1,000 per interest gold (+4,000 at 3-10), Champ Star Level +1,000 / +2,000, Surviving Damage +250 / +1,750,
  Time Bonus +300 to +2,200. So every spare item left on the bench from 2-5 to 3-10 is worth about 37k
  (2,500 × 15 rounds). The way past 900k is to equip only what the board needs and keep the rest on the bench.
- **KEEP / EQUIP line** (`greed.py`, side panel, Tocker's only): above the bench and gold lines. "KEEP" shows your
  bench items' icons and "8 × 2,500 = 20,000 a round · equip from 2-5". From 2-5, "EQUIP" names the few items
  the board needs, made from what's on your bench, with the score the rest keeps ("≈160k by 3-10"). What
  counts as needed: nothing before 2-5; then the main tank's and the main carry's first guide item; 2 each
  from 3-1; full builds from 3-6. Elderwood example at 2-5: Protector's Vow on Hecarim, Last Whisper on
  Ezreal, keep the other 4. Items already on a champion count toward it.
- **Enemy-board screenshots** (`tockershots.py`): Set 18's Tocker's enemy lineups aren't in any readable game
  file. Community Dragon's `map22.bin` has the 30-round lineups for Sets 13–17 only, and the standalone client's
  files are encrypted. So the overlay screenshots every Tocker's round into `data/tockers/<run>/`. It takes a
  "planning" shot 2s in, a "fight" shot 1s after the Combat banner and a "midfight" shot 8s after it. If no
  banner was read by 25s, it takes one "late" shot instead. A `shots.json` records each shot's round, lives,
  level, gold and your traits. Shots are kept until the mode is known, and a lobby game deletes its folder at
  3-1 (by then Tocker's has shown 2-8 to 2-10). A restart in stage 1 starts a new folder. The first version
  decided at 2-1, which wrongly deleted the whole 769k Elderwood run on 2026-10-26: the round reader never read
  Tocker's stage-1 rounds. Tocker's wider top bar puts the round label right at the crop's left edge, and the
  text detector missed it there. `read_round` now pads the crop by 16px; your lobby screenshots still read the
  same. Only the newest 8 runs are kept (`"tockers_shots_keep"`), each
  about 30 MB. `"tockers_shots": false` in config.json turns it off. In Tocker's the Combat banner is looked for
  from 3s into the round instead of 8s, since there's no planning timer. Hand a run's folder to Claude to get
  each round's enemies, star levels, HP and damage.
- **Round by round** (first full capture, 2026-10-26, `data/tockers/20261026-182036/rounds.md`): enemies carry no
  items or one through stage 1, 2 from 2-1, a full 3-item Ahri at 2-3, and 2★ enemies with 3 items in every
  round from 2-6. Lives went at 1-9 (14 flaming archers), 2-3 or 2-4 (2-4 is the Elise round: a life in both
  captured runs), 3-2 or 3-3 (3-3 is Inferno, whose Wound cuts healing by 33%), and the 3-10 dragon boss. So
  hold items through stage 1 and have them on before 2-3. The swarm rounds (1-9, 3-6's 10 Dravens, 3-9's wolf
  pack) need damage that hits many units at once and extra bodies more than items.
- **Lag fix** (2026-10-26, you felt the game lagging; the overlay used about 1.2 CPU cores throughout). The cause was
  the "re-read when something changes" check, which watched the augment cards in the middle of the board. Units are
  always moving there, so it ran a full screen read every 1.5s, in fights too. Now there are no change-driven reads
  during a fight. The augment area is watched only on augment rounds (2-1, 3-2, 4-2). The opponent scout is off in
  Tocker's (no opponents). The Combat banner is checked every 0.8s instead of every loop. In Tocker's, planning
  lasts until the fight starts (no 30s limit).
- **Pause logger** (`pauselog.py`, Tocker's fights): every 2s of each fight, a line in `data/tockers/<run>/
  fights.jsonl` with the fight clock, % of the board that moved, the overlay's longest loop (`overlay_ms`), its CPU
  (`overlay_cpu`, % of one core) and the PC's CPU (`pc_cpu`). A sample is marked `"freeze": "clock"` when the clock
  didn't move in 2s (the game client or your PC froze). It's marked `"freeze": "board"` when the clock ran but
  nothing moved (the fight stalled on the server's side). A big `overlay_ms` at the same moment means the
  overlay was busy. Costs about 33 ms every 2s. `"tockers_pause_log": false` turns it off.
- **Overlay went quiet mid-run** (2026-10-27): it stopped reading 7s into the Riftbeast run at 1-3, and after 1-2 in
  the 20:47 run. Nothing was logged and the cause wasn't found. Two changes:
  - **Window pick** (`capture.py`): the overlay used to take the first window titled "TFT" / "League of Legends (TM)
    Client". A minimised or tiny second match would leave it "Waiting for TFT window…" all game. It now takes the
    largest window that isn't minimised; on a tie, the topmost.
  - **Watchdog** (`watchdog.py`): all of these go to `data/overlay_errors.log`.
    - If the read loop doesn't come round for 30s: "overlay stalled", every thread's stack, then "recovered after
      Ns".
    - If a call holds the interpreter lock so the watcher can't run, faulthandler dumps the stacks after 90s under
      "Timeout (0:01:30)!".
    - Mid-game, the game window gone 15s, and when it comes back: the windows it could see.
    - The round label unreadable for 60s: the crop is saved to `data/round_unread.png`.
    - ❚❚ / ▶ pressed.
    - A game ending also logs one "window gone" line, and the end screen one "unreadable" line; those are normal.
- **Riftbeast doesn't work in Tocker's** (your runs): earlier runs fell off before 2-5 and didn't beat the Elise
  round. The one that finished scored 750,900 (2026-10-27) with 12 units on the board, about 115k under the Elderwood
  best. Don't use it as the no-item build.

## Exact comps from teamfight.lol

**Kept up to date automatically:** every time the overlay starts it runs
`py tools/import_teamfight.py --sync` in the background: the builds are made to match
bunnymuffins.lol/meta (new links imported, every build re-fetched from teamfight.lol, tiers from the page,
builds no longer listed removed, names as bunnymuffins writes them — "Defender Cassiopeia"). The status bar
says what changed; the overlay reloads the builds without a restart. Your `data/custom_comps.json` is never
touched. Offline, it keeps the saved builds.

bunnymuffins marks builds with a big requirement with `*` and writes their "Conditions". Those builds are
capped at B until any one condition is met — e.g. Kha'Zix Reroll: you have Kha'Zix (or 4 finished items);
Invoker Ahri Morgana: 4 finished items; Veigar: an emblem, an artifact, Consuming Flora or early LeBlanc/Ornn;
Adaptor: 2+ bows; Solar Yunara: 50 gold or 4 items. Then the build plays at bunnymuffins' tier. (Your hand
caps in `comp_overrides.json` still apply on top.) What a condition says makes the build *better* ("Invoker
Emblem Ahri bumps this up to S tier", "Artifacts bump this up a tier", "S tier with …", "great with emblems")
is a bonus, not a requirement. Finished items count once two reads in a row agree, and stay counted for the
game, so one missed read doesn't flip a "lots of items" build between A and B.

The sync also takes each build's **How to Play** from its bunnymuffins card: the gameplan, the line for each
stage ("Stage 3: save HP, roll down on 4-2 for Ahri 2 and Morgana 2"), tips, item holders, the best emblem
holder and each carry's items with alternatives (his **bold** must-have items get an asterisk). TFT Academy
builds bring their stage tips. Once you pin a build, only the **current stage's** advice shows and it swaps
as the game moves on: in max mode right under the leveling chips (the chip you're on is the one it follows;
hover another chip to read its stage), in compact mode at the top of the side panel. The side panel adds item
holders / emblem, the gameplan (stages 1–2), tips and items, as much as fits. In the item builds, the guide's
must-have items have a gold asterisk on their corner (Azir: Gunblade; Rammus: Crownguard, Gargoyle's).

The sync also takes each build's **Leveling Pattern** from bunnymuffins (win / lose / mixed streak rows,
e.g. "Lose Streak: 2-3 Lv 4 … 3-5 Lv 7 (40g+), 5+ Lv 8"; 1-cost rerolls: "don't press level until you 3★").
The plan chips show the row for your current streak; the build badge ("3-COST REROLL · roll at Lv 7",
"FAST 8/9") comes from his build type. In reroll builds the guide's 3★ units lead the item builds (★★★).

Paste teamfight.lol team-builder links (one per line) into `data/teamfight_links.txt`, then:
```
py tools/import_teamfight.py
```
Each comp gets teamfight.lol's exact board — hex positions, 3★ units, items on every unit — plus
its best augments. Max mode draws that board; item advice uses exactly those items. Leveling is
a standard timeline per comp style (teamfight.lol doesn't publish leveling). A carry holding
3 items counts as finished even if you built different items.

## More builds from TFT Academy (dropped 2026-10-24)

**No longer used** — your call: too many gimmicky capped final boards and emblem builds. Startup no longer syncs it and its builds aren't loaded (`OTHER_GUIDES = []` in `gamedata.py`); your own builds keep the openers they copied from it. What it did:

After the bunnymuffins sync, startup also ran `py tools/import_tftacademy.py --sync`: TFT Academy's tier list
(tftacademy.com/tierlist/comps, written by pro players) — its public **S and A** builds, with their exact board,
items, 3★ units, style, augments and stage tips, into `data/comps_tftacademy.json` (rewritten by every sync;
put your own builds in `custom_comps.json`). They show as "Name — TFT Academy". Lower tiers aren't imported:
bunnymuffins covers them, and more B reroll builds would crowd the S/A ones out of the picks.

A build both guides list (mostly the same units, sharing a carry) shows once: the higher-rated one after your
`comp_overrides.json` caps and the ranked-results cap below; a tie keeps bunnymuffins' (it has leveling patterns
and conditions). A build tagged as needing an emblem / augment is its own variant, never merged with one that
doesn't. The kept build's notes say what the other guide rates it ("TFT Academy: A tier (Draven Ezreal 9)"). If
the kept board has no Elderwood tree / plant, it takes their spots from the other guide's board. An emblem on a
TFT Academy board is its best case, not a requirement (it puts builds that depend on one in tier X).

(tftactics.gg was tried as a third guide and dropped: 11 of its 33 S/A builds finish top 4 under 50% of the time.)

## Tiers capped by real results (SeeMeta)

The last startup sync, `py tools/import_seemeta.py --sync`, saves SeeMeta's Set 18 tier list — tens of
thousands of ranked matches: average place, top-4 and win rate per comp — to `data/seemeta.json`. Every guide
build is matched to a SeeMeta comp by shared units (5+) and its letter is **capped by that comp's real top-4
rate**: 56%+ S, 52%+ A, 48%+ B, 44%+ C, below that D (4 of 8 players make top 4, so 50% is a coin flip). A build
SeeMeta doesn't list (niche, emblem builds) isn't proven and shows at most B. The cap only lowers a letter, its
reason goes at the front of the notes ("bunnymuffins S → A, ranked: top 4 52%, avg 4.40 (Lunar Kha'Zix)"), and
your own `custom_comps.json` builds are left alone. Constants: `TOP4_TIER` in `tft_overlay/gamedata.py`.

## Game history (your augments, build, HP)

The live overlay keeps one line per game in `data/games/history.jsonl`: the augments you were offered (rerolls
too) and took, your build (pinned, or the top suggestion if you never pinned), the round you went out on, your HP
/ level / gold at the start of each round, your final traits and what you bought. The game in progress is saved
as it goes (`data/games/current.json`), so restarting the overlay within 15 minutes carries on with the same
game. A game is filed when the next one starts or the TFT window has been gone 2 minutes.
```
py tools/game_history.py        # last 10 games (py tools/game_history.py 30 for more)
```
lolchess.gg and similar sites don't show augments for normal games — this does.

Your own builds go in `data/custom_comps.json` (same format as comps.json; "Double LeBlanc (yours)" was made from
your 19 Oct 1st place via lolchess.gg). Add `"special_items_optional": true` when an emblem / artifact on the board
was an extra that game rather than something the build needs.

## Shop logger (is the shop matching its odds?)

The live overlay records every shop you see — including each reroll of a roll-down — with your
level, gold and the posted odds, to `data/shop_log/<date>.jsonl`. Nothing is shown in game, and no
OCR runs during a roll-down (pixel checks only; shops are read once things go quiet, ~0.2s each).

```
py run.py --log-only        # just log, no window
py tools/shop_report.py     # after a few games
```

The report compares the cost share you actually saw with the posted odds per level, checks whether
4-costs you'd bought show up more than they should, and how often units repeat in back-to-back
shops — each with a z-score so luck and a real pattern can be told apart.

## Update for a new patch / set

**Automatic.** The first startup sync is `py tools/update_game_data.py --sync`: one small request asks
Community Dragon which patch its game files are for (it publishes a few hours after a patch goes live). When
that's newer than `data/game_data_version.json`, it downloads the dump (`data/cdragon_tft.json`, the old one
kept as `cdragon_tft.prev.json`), takes the newest standard set (`TFTSetNN`), and redoes `set.json` + champion
icons, items + item icons, this set's augment names / descriptions and augment icons. The overlay then restarts
itself on the new data (status: "New patch: … — restarting…"); the guide and SeeMeta syncs follow.

**Radiant items** (`data/radiant.json`, made by `tools/fetch_items.py` with the other item data) have their own
icons. The item reader matches them by those icons and reports each as the item it upgrades, so a Radiant
Deathblade finishes Deathblade. Before 2026-10-26 a Radiant item on a unit didn't match anything. Its unit's items
were dropped from the read, and the overlay kept saying to build an item you already had.

By hand, same thing: `py tools/update_game_data.py --sync` (without `--sync` it only says whether there's a
newer patch). Builds come from the guides' own syncs; your own go in `data/custom_comps.json`.

## Layout

- `tools/update_game_data.py` / `import_teamfight.py` / `import_tftacademy.py` / `import_seemeta.py` – the
  startup syncs (patch game data, bunnymuffins + teamfight.lol, TFT Academy, SeeMeta's ranked results)
- `tft_overlay/guide.py` / `guide_app.py` – static guide data + window (no capture imports)
- `tft_overlay/ui_common.py` – config, colors, icon cache, multi-monitor clamping
- `tft_overlay/slams.py` – comp item names → recipes; SLAM/HOLD, needs, pick ranking
- `tft_overlay/matcher.py` – comp scoring (live) and trait counts
- `tft_overlay/app.py`, `capture.py`, `reader.py`, `items.py` – live mode only
- `tft_overlay/scout.py` – opponent tracking: lobby names, scouted traits, contest check
- `tft_overlay/augment_lamp.py` – the augments you took, from the box above your lamp
- `tft_overlay/roster.py` – which carries you hold, stand-ins for missing ones, core-unit deadlines
- `tests/fixtures/` – real screenshots (board, armory, carousel, augment)
