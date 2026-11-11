# Build an IPA on GitHub

Packing an IPA needs a Mac: Xcode builds the app and Apple's Metal compiler builds its shaders. Without one, your own copy (fork) of this repository on GitHub can do it on GitHub's Macs, free of charge: you paste a download link to your game files, and get the IPA back as a password-protected download. Everything runs on GitHub; from Windows or any other computer you only use the browser.

The workflow is `.github/workflows/build-ipa.yml`; it runs the same script as [Build from the command line](../README.md#build-from-the-command-line).

## What you need

- A GitHub account with a fork of nxnative (the **Fork** button on the repository's page).
- Your `prod.keys`.
- The game's files (the base game, and its update and DLC if you have them) behind a **direct download link**: opened in a browser, the link starts the download at once, with no page or button in between. One link per file, or one `.zip`, `.7z` or `.rar` of the files of one game. NSZ and XCZ files work too, and so does a Switch 2 game's `.nup` container: one file holding the base game, its updates and its DLC.
- **A Switch 2 game** (Donkey Kong Bananza) comes as a dump: a folder, not NSPs. Put the game's folder in a `.zip`, `.7z` or `.rar` (a wrapper folder inside is fine) holding `base/exefs/` (`main`, `main.npdm`, `sdk`, `rtld`), `base/romfs/`, and optionally `updates/<version>/exefs` + `romfs` and `dlc/<DLC title ID>/<n>/romfs` ([the layout](../README.md#switch-2-games)). The workflow recognizes it by that layout, and the build needs no `prod.keys` for it; base, update and DLC may also come as separate archives, one link each. Or it comes as one `.nup` container ([NUP](https://github.com/a55uka/nup)): a single link, the base game, updates and DLC inside, no `prod.keys` needed either. Nothing is selected in the form. The game's profile must exist in [dm/games](../dm/games/README.md#games). The upstream project tests Switch 2 games on a Mac only: an iPhone app of one is untested.
- [7-Zip](https://www.7-zip.org) (or another tool that opens password-protected `.7z` files), to open the download.
- A sideloading tool for the IPA (AltStore, Sideloadly, LiveContainer, …).

## Set it up once

1. **Turn on Actions** in your fork: open its **Actions** tab and confirm that you want to run its workflows (GitHub turns them off in a new fork).
2. **Add the secrets**: in your fork, **Settings → Secrets and variables → Actions → New repository secret**, once for each:
   - `PROD_KEYS`: open `prod.keys` in a text editor (Notepad), copy all of it and paste it.
   - `IPA_PASSWORD`: a password you make up. The IPA comes back encrypted with it, and you type it in 7-Zip to open it. Make it long: anyone signed in to GitHub can download the encrypted file.
   - `GAME_LINK` (optional): a link to use when the run's link field is empty ([What others can see](#what-others-can-see)).

   A secret is never shown again, not even to you, nor in a run's log; you can replace it with a new value at any time.
3. The **Run workflow** button appears only for a workflow on the fork's default branch (`main`): if the Actions tab does not list **Build IPA**, bring this workflow into `main` first.

## Check the runner

Before the first game, run it once with **Check the runner only** ticked (no link needed). In a few minutes its summary shows the runner's free disk space after the cleanup, the largest game that should fit, and the versions of Xcode, the Metal compiler and .NET. A build needs free disk space of about three times the game files' size plus 10 GB.

## Build

1. In your fork: **Actions → Build IPA → Run workflow**.
2. Fill in the form:

   | Field | |
   |---|---|
   | Download link | the link to your game files; several links are separated by spaces |
   | Title ID | only when a run says it could not read the game's title ID (a cartridge dump `.xci` whose file name has none, such as `Game (World) (Rev 1).xci`): the base game's 16 digits, e.g. `010028600EBDA000` (the games table in [dm/games](../dm/games/README.md#games) lists them). Empty otherwise: the run reads the title ID from what it downloaded, and names the build cache after it |
   | Game language | `ja`, `fr`, `de`, `es`, `zh-Hans`, … (empty: English); it must be one the game has |
   | App name | the name on the home screen (empty: the game's name) |
   | Version | the version to pack, e.g. `v0` (empty: the newest in the download) |
   | Skip the disk cleanup | saves about 5 minutes: without the cleanup the runner has about 95 GB free, enough for a game of up to about 25 GB; leave it off for a bigger one |
   | Show the frame rate | the frame counter at the top left on the iPhone |
   | Minutes before a pause | `285` by default: see [Builds longer than six hours](#builds-longer-than-six-hours) |
   | Attempts | `8` by default: the most attempts of the run, the first one counted |

3. Click **Run workflow**. The run appears in the list; you can close the browser meanwhile, and GitHub emails you when a run fails.

GitHub's Mac has 3 cores, so a build takes longer than on a recent Mac. A run stops at 6 hours: [Builds longer than six hours](#builds-longer-than-six-hours) covers what happens then.

## Get the IPA

1. Open the finished run. Its summary, **Your IPA**, links the download, `<name>.ipa.7z`, which is also under **Artifacts** at the bottom.
2. Download it (you must be signed in to GitHub). It is deleted after one day; run the build again for a new one.
3. Open it with 7-Zip, type your `IPA_PASSWORD`, and extract the `.ipa`.
4. Install the IPA with your sideloading tool, as described under [Install](../README.md#install) (the Increased Memory Limit capability matters there too).

## What others can see

Your fork is public, and so are its runs:

- **Shown**: that a run happened, its options, and its log: the game's name, title ID, file names and sizes, and how long each step took.
- **Never shown**: your keys, your password, and the game files. The IPA is encrypted, its file name inside too, so the `.7z` anyone signed in can download is of no use without the password.
- **The link** is hidden in the log. To keep it out of the run entirely, put it in the `GAME_LINK` secret and leave the link field empty. A link that expires, or a file you delete after the build, is safer either way.

## Limits

- **Disk space**: about three times the game files' size plus 10 GB; [Check the runner](#check-the-runner) says what is free. The biggest games may not fit.
- **A dump needs more disk than NSPs do** (the archive, its unpacked folder, a RomFS image of it, then the IPA): leave **Skip the disk cleanup** off, and check the summary's lowest free disk space.
- **6 hours** per job: a bigger game is built in slices instead, see below.
- **Fire Emblem: Three Houses, Portal and Catherine: Full Body** cannot be built here: their build boots the game to capture its shaders, which needs Metal 4, and GitHub's Macs are virtual Macs without it.
- One game per run.

## Builds longer than six hours

A big game compiles its shaders for hours: one run of a Switch 2 game reached 16 percent of them in five hours, which is about thirty hours. A job stops at six, so the build pauses and carries on by itself.

- **Minutes before a pause** (285 by default) is when the build stops on purpose: early enough that saving the cache still fits in the six hours. Set it to `0` to never pause, which is what you want for a game that finishes in one job.
- At that minute the build stops, the shader cache it compiled so far is saved as a cache release (`cache-<title ID>-latest`), and the run's summary says the attempt paused.
- `.github/workflows/chain-controller.yml` sees that and re-runs the job. A re-run keeps the run's commit, its inputs and its secrets, so pushing to your fork during those thirty hours does not move the build onto other code: the next attempt starts where the last one stopped.
- **Attempts** (8 by default) caps the chain, about 38 hours of building. When it is reached the run stops with the cache saved: run the build again with the same game to continue from it.
- The chain also stops by itself when an attempt saved no more cache than the one before it, which means the build is stuck, and it sends no further attempts.

A download link often expires within hours, and a later attempt comes back to the download hours later, so the files the first attempt downloaded are kept in a release of the run (`x-input-<run number>`), encrypted with the **IPA password** and split under GitHub's 2 GiB asset limit (it sits in the repository's Releases page, tagged with the run's number, one asset per part). A later attempt takes them from there and never touches the link again. The release is deleted when the build finishes or its chain gives up, and one left behind by a cancelled run is swept by the next build, a day later. Turn **Keep the download for later attempts** off to download afresh each attempt; nothing in that path can fail a build, since a stash that cannot be read is downloaded over.

The only thing to do during a chain is nothing. Leave the run alone: its summary gains a **Continuing the build** entry at each pause, and the final attempt publishes the IPA as usual. The cache release gets big (3 to 12 GB for the games built so far), which is what makes the next attempt cheap: it restores that cache and only compiles what is missing.

A pause costs whatever the attempt had in flight. The cache counts a shader as reusable only once the build has finished it, and the part-finished outputs of the shader it was still working on stay in a staging area that the next build clears at its start. Two shader caches taken from a stopped two-shard build showed the size of that loss: 9,186 entries, of which 58 were finished shaders, and a resumed attempt then reported `4,963 not cached` out of 5,021. So each build now ends with three lines that say what the cache did for it:

```
Cache accounting: after the build  total=27894 index=73 shader=300 metallib=5000 stage=22500
Cache accounting: before the build total=9186 index=58 shader=256 metallib=2537 stage=6335
Cache accounting: added index +15 (reusable), staged +16165 (thrown away), metallib +2463, shader outputs +44, total +18708
```

`index` is the finished shaders, the only count that makes the next attempt cheaper. `staged` is work this attempt paid for and the next one deletes. A build that adds thousands to `staged` and a handful to `index` is compiling twice for the same result, however large its cache release looks.

A sharded build ends each shard when it has compiled its share, and a share is a partial build: a build that does not finish the whole map writes no library stamp, so the script refuses it (`no stamped Mac shader outputs … did not finish a full build`) even when the shard reached its last shader. That is the shard's normal end, not a failure. When the tool's own build report (`_b_build.json`, written after its compile loop, one row per shader) says every shader in the share came out `ok`, the shard job reports the refusal as a warning, saves its cache, and the finish job merges the shard caches, makes the stamp from them and packs the app. Not the progress file: `dmsh_build.py` deletes that one on its way out, so a shard that finished its pass leaves none behind and the earlier check read that as "no work done". A share that stops short is still a failure, and it now saves its cache as well: an attempt saves what it compiled when it fails, not only when it succeeds, because a cache left unsaved is a cache the next attempt pays for again. A cancelled run is the exception, since its steps never reach the save.

## When a build fails

The run's summary and the last lines of the step that failed say why. Common ones:

| Message | What to do |
|---|---|
| the link gave a web page, not a file | the link is not a direct download link |
| curl exit 22 | the server refused the link: it expired, or it needs a browser |
| no NSP with a readable title ID | the game is a cartridge dump (`.xci`) without a `[title ID]` in its name: fill in the **Title ID** field |
| the archive is password-protected | pack the files without a password |
| more than one game in the folder | one game per run |
| the archive holds N files and none is an NSP, XCI, NSZ, XCZ or NUP | an extracted game folder that is not a dump in the layout above (no `base/exefs/main.npdm` with `base/romfs`): repack it in that layout |
| No space left on device, or a build that stops while the summary's lowest free disk space is near 0 | the game is too big for the runner |
| missing repository secrets | add `PROD_KEYS` and `IPA_PASSWORD` ([Set it up once](#set-it-up-once)) |

The **build-logs** artifact of a failed build (kept 7 days) holds each step's full log. For a crash or a wrong picture in the game itself, see [the symptom index](agents/symptoms.md).
