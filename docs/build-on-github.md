# Build an IPA on GitHub

Packing an IPA needs a Mac: Xcode builds the app and Apple's Metal compiler builds its shaders. Without one, your own copy (fork) of this repository on GitHub can do it on GitHub's Macs, free of charge: you paste a download link to your game files, and get the IPA back as a password-protected download. Everything runs on GitHub; from Windows or any other computer you only use the browser.

The workflow is `.github/workflows/build-ipa.yml`; it runs the same script as [Pack an IPA](../README.md#pack-an-ipa).

## What you need

- A GitHub account with a fork of nxnative (the **Fork** button on the repository's page).
- Your `prod.keys`.
- The game's files (the base game, and its update and DLC if you have them) behind a **direct download link**: opened in a browser, the link starts the download at once, with no page or button in between. One link per file, or one `.zip`, `.7z` or `.rar` of the files of one game. NSZ and XCZ files work too.
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
   | Game language | `ja`, `fr`, `de`, `es`, `zh-Hans`, … (empty: English); it must be one the game has |
   | App name | the name on the home screen (empty: the game's name) |
   | Version | the version to pack, e.g. `v0` (empty: the newest in the download) |
   | Show the frame rate | the frame counter at the top left on the iPhone |

3. Click **Run workflow**. The run appears in the list; you can close the browser meanwhile, and GitHub emails you when a run fails.

GitHub's Mac has 3 cores, so a build takes longer than on a recent Mac, and every run starts from nothing: nothing of an earlier build is reused. A run stops after 6 hours.

## Get the IPA

1. Open the finished run. Its summary, **Your IPA**, links the download, `<name>.ipa.7z`, which is also under **Artifacts** at the bottom.
2. Download it (you must be signed in to GitHub). It is deleted after one day; run the build again for a new one.
3. Open it with 7-Zip, type your `IPA_PASSWORD`, and extract the `.ipa`.
4. Install the IPA with your sideloading tool, as in step 4 of [Pack an IPA](../README.md#pack-an-ipa) (the Increased Memory Limit capability matters there too).

## What others can see

Your fork is public, and so are its runs:

- **Shown**: that a run happened, its options, and its log: the game's name, title ID, file names and sizes, and how long each step took.
- **Never shown**: your keys, your password, and the game files. The IPA is encrypted, its file name inside too, so the `.7z` anyone signed in can download is of no use without the password.
- **The link** is hidden in the log. To keep it out of the run entirely, put it in the `GAME_LINK` secret and leave the link field empty. A link that expires, or a file you delete after the build, is safer either way.

## Limits

- **Disk space**: about three times the game files' size plus 10 GB; [Check the runner](#check-the-runner) says what is free. The biggest games may not fit.
- **6 hours** per run.
- **Fire Emblem: Three Houses, Portal and Catherine: Full Body** cannot be built here: their build boots the game to capture its shaders, which needs Metal 4, and GitHub's Macs are virtual Macs without it.
- One game per run.

## When a build fails

The run's summary and the last lines of the step that failed say why. Common ones:

| Message | What to do |
|---|---|
| the link gave a web page, not a file | the link is not a direct download link |
| curl exit 22 | the server refused the link: it expired, or it needs a browser |
| the archive is password-protected | pack the files without a password |
| more than one game in the folder | one game per run |
| No space left on device, or a build that stops while the summary's lowest free disk space is near 0 | the game is too big for the runner |
| missing repository secrets | add `PROD_KEYS` and `IPA_PASSWORD` ([Set it up once](#set-it-up-once)) |

The **build-logs** artifact of a failed build (kept 7 days) holds each step's full log. For a crash or a wrong picture in the game itself, see [the symptom index](agents/symptoms.md).
