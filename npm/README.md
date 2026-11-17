# frontier-orchestration npx installer (prototype)

This folder holds a prototype of a one-command installer. It is **not published**.
`package.json` has `"private": true`, so `npm publish` refuses to publish it.

The goal: a user runs one command. npx downloads the package. The package unpacks the plugin and
installs it into every agent host that it finds on the machine.

## Commands

After a publish (not done), the command would be:

```bash
npx frontier-orchestration            # same as: npx frontier-orchestration install
npx frontier-orchestration uninstall
npx frontier-orchestration doctor --live
```

Today you can try it from a local tarball. Run these commands in `npm/`:

```bash
npm pack                                      # runs scripts/bundle.mjs, writes frontier-orchestration-0.1.0.tgz
npx --package=./frontier-orchestration-0.1.0.tgz frontier-orchestration install --dry-run
```

Or run the bin from a checkout, after you build the bundle:

```bash
node scripts/bundle.mjs
node bin/cli.mjs install --dry-run
```

Flags:

| Flag | What it does |
| --- | --- |
| `--hosts=claude,codex,agy,skills` | Use only these hosts. For `install`, the default is every host that is found. For `uninstall`, the default is every host in the record (see below). `--hosts` cannot add a host that is not in the record. |
| `--dry-run` | Print every step. Change nothing. |
| `--yes`, `-y` | Do not ask before changes. Without a terminal, `--yes` is necessary. |
| `--no-setup` | Do not start the Python setup after the install. |
| `--live` | `doctor` only: pass `--live` to `fo.py doctor`. |
| `--force` | `uninstall` only. See "What `uninstall` and `doctor` do". Without a record, `--force` together with `--hosts` is refused (exit 2). |
| `--break-lock` | `install` and `uninstall` only: delete the lock file first (see "The lock"). Use it only when no installer is running. It cannot be used with `--dry-run`. |

## What `install` does

1. **Check Python.** It looks for `python3` on `PATH` and asks it for its version. The skill
   needs Python 3.9 or newer. If Python is missing or too old, the installer stops and prints the
   fix. It changes nothing before this check.
2. **Copy the plugin to a stable folder.** The package holds a bundle (see below). The installer
   copies the bundle to `~/.local/share/frontier-orchestration/plugin/<version>/`. If
   `XDG_DATA_HOME` is an absolute path, the folder is under it instead, as in `fo_config.py`. The
   copy is atomic (all or nothing): the installer copies into a temporary folder next to the
   target, then renames it into place. The copy gets the marker file
   `.installed-by-frontier-orchestration-npx`. If the copy fails, the target does not change. Older
   versions stay until `uninstall`. Each run first deletes the leftovers of an earlier run that
   was stopped. A leftover name is `.tmp-<target name>-`, then the 6 characters that the
   temporary folder gets, then `.old` or nothing. The installer deletes only exact matches. Thus
   an install of `0.1.0` never deletes a leftover of `0.1.0-beta`. It deletes a leftover only
   when the leftover has the marker file or is empty (see "What the installer deletes"). If a
   version folder exists without the marker file, the install stops with exit 1 and does not
   change that folder.
3. **Register the plugin with each host.** A host is "found" when its CLI is on `PATH`. The
   `skills` host is found when `~/.agents/skills` exists.

   | Host | Commands |
   | --- | --- |
   | Claude Code | `claude plugin marketplace add <folder>`, then `claude plugin install frontier-orchestration@frontier-orchestration` |
   | Codex | `codex plugin marketplace add <folder>`, then `codex plugin add frontier-orchestration@frontier-orchestration` |
   | Antigravity | `agy plugin install <folder>` |
   | skills | Copy `skills/frontier-orchestration` to `~/.agents/skills/frontier-orchestration` |

   A failing host is reported and skipped. It does not stop the other hosts. If any host fails,
   the exit code is 1. A failed `marketplace add` is only a warning. The next step for that host
   still runs.

   **Ownership first.** Before the first command that changes a host, the installer reads the
   list of that host for each step of the host. It does this for every host:

   | Host | Lists that it reads (in this order) | First command that changes the host |
   | --- | --- | --- |
   | Claude Code | `claude plugin marketplace list --json`, `claude plugin list --json` | `marketplace add` |
   | Codex | `codex plugin marketplace list --json`, `codex plugin list --json` | `marketplace add` |
   | Antigravity | `agy plugin list --json` | `plugin install` |

   In each list, the installer looks for the entry named `frontier-orchestration` (on Claude
   Code and Codex, the plugin entry is `frontier-orchestration@frontier-orchestration`). It
   reads the location of the entry: `installLocation` (Claude Code marketplace), `root` (Codex
   marketplace), `path` (agy plugin), or the plugin location fields. A location belongs to this
   installer only when it is a folder that holds the marker file
   `.installed-by-frontier-orchestration-npx`. The installer does not follow a symbolic link for
   this check. The place of the folder does not count: a folder in
   `~/.local/share/frontier-orchestration/plugin/` without the marker file (for example your
   own checkout) does not belong to this installer. On Claude Code and Codex, a plugin whose
   own location has no marker file belongs to the owner of its marketplace. `agy plugin install` copies the
   version folder, with the marker file, to `~/.gemini/config/plugins/frontier-orchestration`.
   Thus a copy that agy made from this installer has the marker file, and your own agy plugin
   does not. The installer uses the same check before each remove command at uninstall, and
   before it deletes a folder.

   - No entry with that name, or an entry whose location belongs to this installer (for
     example an earlier version): the installer runs the steps of that host.
   - An entry whose location does not belong to this installer: the item is from another
     source (for example a marketplace from GitHub, or your own edited agy plugin). The
     installer runs no command that changes that host, records nothing for that host, and
     skips that host. The summary says `skipped`, gives the location, and gives the command
     that removes that item, if you want this copy instead.
   - The list cannot tell: the list command fails, its output is not a list that the installer
     knows, or the entry has no location. A Claude Code or Codex plugin entry whose marketplace
     is not listed is in this group too. The installer does not change that host and records
     nothing for it. The summary says why.

   The installer does not install the plugin in these cases. On Claude Code and Codex, the
   plugin would come from the other marketplace, not from the copy of this installer. On agy,
   `plugin install` would replace your plugin, and a later `uninstall` would remove it. A
   skipped host does not change the exit code. The `skills` copy has its own check (see below)
   before the installer changes anything in `~/.agents/skills`.

   A host CLI gets no input: the installer closes its standard input at once. Only a host CLI
   that reads its answer from standard input gets end of input and stops at once. A host CLI
   that asks on `/dev/tty` (the terminal) still waits until the 5-minute timeout. At the
   timeout, the installer sends SIGTERM, then SIGKILL after 2 seconds, and waits until the
   command has exited. The step then fails with exit 124 and "(timeout)", also when the command
   exits 0 after SIGTERM.

   The `skills` copy has a marker file, `.installed-by-frontier-orchestration-npx`. If
   `~/.agents/skills/frontier-orchestration` exists without that marker, another tool (for
   example skills.sh) put it there. The installer then leaves it alone and says so. The
   temporary copy for this host is made in `~/.agents`, not in `~/.agents/skills`. Thus a run
   that is stopped never leaves a second `SKILL.md` in the folder that agents scan. If
   `~/.agents` is on a different file system, the temporary copy is made next to the target.

   **The record.** The installer keeps a record of each step in
   `~/.local/share/frontier-orchestration/plugin/installed-hosts.json` (or the same path under
   `XDG_DATA_HOME`), for example:

   ```json
   {"hosts": {"claude": {"marketplace": "done", "plugin": "pending"}, "skills": {"copy": "done"}}}
   ```

   The installer writes the record after the copy, before the first host step. Before each
   step runs, the record gets that step as `pending`. When the step succeeds, the record gets it
   as `done`. Each write is atomic: a temporary file, then a rename. Thus a run that you stop
   (for example with Ctrl-C) leaves a record of each step that can be registered. A step that
   fails stays `pending`. `uninstall` counts a `pending` step as possibly registered. A step
   that is `done` stays `done` in a later install. If the installer cannot write the record, it
   stops and runs no more host steps. If the record cannot be read, `install` changes nothing
   and tells you to repair or delete the file. A dry run writes no record.
4. **Start the setup.** The installer runs
   `python3 -I <folder>/skills/frontier-orchestration/scripts/fo.py install` with the terminal
   attached, so you get the interactive installer. It skips this step with `--no-setup`, or when
   the input is not a terminal. In both cases it prints the command, so you can run it later.

**The lock.** Only one `install` or `uninstall` runs at a time. Two runs at once can lose
record updates (each run reads, changes and writes the record), and an `uninstall` can delete
the plugin folder while an `install` registers a host with it. Thus `install` (from the
question "Continue?" to the end of step 3) and `uninstall` (from the record read, through the
question, to the folder delete) hold a lock file,
`~/.local/share/frontier-orchestration/.installer.lock` (in the parent of the plugin root
folder). The installer creates it with the `wx` flag, so the create fails when the file exists.
The file holds the process id (pid), the start time and a random token of the run.

- If the lock file exists, the run does not wait. It changes nothing, prints the pid and the
  start time in the lock file, and exits 1. The message says: if no installer is running, run
  the command again with `--break-lock`.
- The installer never takes over a lock by itself, also when the pid in the lock is not alive.
  Only you can know that no installer runs.
- `--break-lock` deletes the lock file one time, then creates it with the `wx` flag. If another
  run creates the lock file between these two actions, the create fails and the run exits 1.
- The run deletes the lock file when it ends.
- On SIGINT (Ctrl-C) or SIGTERM, the run first stops the host command that runs now: SIGTERM,
  then SIGKILL after 2 seconds. It waits until that command has exited. Then it deletes the lock
  file, and the signal stops the run. The run starts no new command after the signal.

The run takes the lock before it asks "Continue?", so no other run can change the record or
the plugin folder between your answer and the work. A second run that starts while the question
waits exits 1. Every `uninstall` that is not a dry run takes the lock, also when
`~/.local/share/frontier-orchestration` does not exist yet: the run then creates that folder for
the lock, and deletes it again at the end when it is empty. A run that must ask but has no
terminal and no `--yes` stops before the lock (exit 2). A dry run takes no lock. `install`
reads the record under the lock. The interactive setup (step 4) runs after the lock is released.

## What `uninstall` and `doctor` do

`uninstall` reads the record. It works only on a step in the record (`done` or `pending`). A
step that this installer did not run is not in the record, so `uninstall` does not touch it.
`--hosts` limits the hosts to remove; it cannot add a host that is not in the record.

The record alone does not prove that this installer owns what is on the host now. For example,
you can replace the marketplace with one from GitHub after the install. Thus, before each remove
command, `uninstall` reads the list of that host again (the check command below) and finds the
owner of the step:

- The list does not show the step: the step is gone. It leaves the record. No remove command
  runs.
- The location of the step belongs to this installer (a folder, not a symbolic link, that
  holds the marker file): the remove command runs. It is the same check as at install.
- The location does not belong to this installer: the step is from another source. `uninstall`
  keeps it on the host, removes the step from the record, and says so. A folder in the plugin
  folder without the marker file is from another source too. For example, if you point the
  marketplace at your own checkout in `~/.local/share/frontier-orchestration/plugin/`,
  `uninstall` runs no remove command for that host and keeps your checkout.
- The owner cannot be found (the CLI is not on `PATH`, the list cannot be read, or it gives no
  location): no remove command runs. The step stays in the record (see "A step that is not
  finished" below).

The locations come from these fields: `installLocation` (Claude Code marketplace), `root`
(Codex marketplace), `source.path` and `marketplaceSource.source` (Codex plugin), `path` (agy
plugin). Claude Code keeps a plugin in its own cache folder, so for a Claude Code or Codex
plugin whose location does not tell, the owner of its marketplace (the part after `@` in the
plugin id) decides. `agy plugin install <folder>` copies the folder, with the marker file, to
its own folder (`~/.gemini/config/plugins/<name>`), so the marker file decides for agy. While
the `plugin` step of a host stays in the record, `uninstall` does not remove the marketplace of
that host: the owner of the plugin is found through it.

A host with a step from another source shows `left` in the summary. That step does not use
the plugin folder, so it does not stop the delete of the folder.

| Host | Step | Remove command | Check command |
| --- | --- | --- | --- |
| Claude Code | `plugin` | `claude plugin uninstall frontier-orchestration@frontier-orchestration` | `claude plugin list --json` |
| Claude Code | `marketplace` | `claude plugin marketplace remove frontier-orchestration` | `claude plugin marketplace list --json` |
| Codex | `plugin` | `codex plugin remove frontier-orchestration@frontier-orchestration` | `codex plugin list --json` |
| Codex | `marketplace` | `codex plugin marketplace remove frontier-orchestration` | `codex plugin marketplace list --json` |
| Antigravity | `plugin` | `agy plugin uninstall frontier-orchestration` | `agy plugin list --json` |
| skills | `copy` | Delete `~/.agents/skills/frontier-orchestration`, only if it has the marker file | The folder exists |

The check commands come from the `--help` output of each real CLI. They only read. The output
of `agy plugin list --json` is an array of objects, for example
`{"name": "frontier-orchestration", "enabled": true, "version": "0.1.0", "source": "...", "path": "..."}`.
The check reads the `name` field, and the `path` field for the owner.

The Claude Code and Codex CLIs exit with an error when you tell them to remove something that is
not there. `agy plugin uninstall` exits 0 for a plugin that is not there. Thus `uninstall` does
not trust the exit code alone. After each remove command, it runs the check command of that
step again:

- The step is not in the list: the step is gone. The step leaves the record.
- The step is still in the list: the removal failed. The step stays in the record.
- The list cannot be read: the removal is not checked, also when the remove command exits 0.
  The step stays in the record.

Thus only a step that the list shows as gone, or as from another source, leaves the record.

The list "cannot be read" when the check command fails, when its output is not JSON or not a
list, or when the list has entries but no entry has a field that the installer knows (`id`,
`pluginId` or `name`). An empty list means "not there".

The record is written after each step. The removal is best effort: a failure is reported, and
the next command still runs. Thus a second run tries only the steps that are still in the
record, and a run with no failure exits 0.

Then `uninstall` deletes the items that it made in `~/.local/share/frontier-orchestration/plugin/`,
then the record, then the folder (see "What the installer deletes"). It does this only when
the record is empty. It keeps that folder and the record in these cases, because a host can
still use the folder:

- A step is not finished: it cannot be checked, it is still in the list, or it is a marketplace
  step that waits for its plugin step. The exit code is then 1 (see below).
- `--hosts` did not name every host in the record. The exit code is 0.

**A step that is not finished.** `uninstall` exits 1 and prints each step that stays, with its
cause and the command that removes it by hand. Then it prints how to finish:

- Run `uninstall` again later, for example when the host can list its plugins again. It tries
  only the steps that are still in the record.
- Or run `uninstall --force`. It removes each step that it cannot check from the record, but it
  runs no remove command for a step whose owner it cannot find. The host keeps that step as it
  is, and the output names the remove command. When no other step stays, `--force` deletes the
  folder and the record as "What the installer deletes" says, and exits 0.

`--force` does not remove a step that is still in the list from the record. Remove that step by
hand, then run `uninstall` again.

**No record.** If there is no record but a version folder exists (for example
`plugin/0.1.0/`), a host can still point at it. `uninstall` then changes nothing, tells you to
run `frontier-orchestration uninstall --force`, and exits 1. With `--force` and no record,
`uninstall` runs each remove command (and its check) for every host that is found, then deletes
the folder as "What the installer deletes" says. If a host still lists the plugin after that, the folder stays, the exit code is 1,
and the note names that host. Without a record, `--force` together with `--hosts` is refused: a host outside
`--hosts` can still point at the folder that `--force` deletes. `uninstall` then changes nothing
and exits 2. If there is no record and no version folder, `uninstall` changes no host. It
deletes only the items that it made in the plugin folder and exits 0. If an item that it did
not make stays, it exits 1.

## What the installer deletes

The installer deletes a folder and its contents only in these two cases:

- The folder has the marker file `.installed-by-frontier-orchestration-npx`. Each version
  folder, the `skills` copy, and each temporary folder of the copy get this file. The
  temporary folder gets it before the copy starts.
- The folder is a temporary folder that this run made.

The installer deletes `~/.local/share/frontier-orchestration/plugin/` itself only when that
folder is empty. It uses an empty-folder delete (`rmdir`), never a delete of the contents. The
order is:

1. Delete each version folder and each leftover folder that has the marker file. Delete each
   empty leftover folder and each temporary record file (`installed-hosts.json.tmp-<pid>`).
2. Delete the record. If a step 1 delete failed, keep the record.
3. Delete the plugin folder, if it is empty now. The lock file is not in this folder.

The installer keeps every other item in the plugin folder: for example a folder without the
marker file (also a version folder or a Git checkout), or a file that you put there. It then
also keeps the plugin folder. `uninstall` writes `left in place: <items>` and exits 1. Move or
delete these items, then run `uninstall` again. `--force` does not change this rule.

`install` and `uninstall` also never replace or delete a folder at
`~/.agents/skills/frontier-orchestration` or `plugin/<version>/` without the marker file.

If the record cannot be read, `uninstall` changes nothing and tells you to repair or delete it.

It never touches your config (`~/.config/frontier-orchestration/`) or your ledger (by default
`~/.local/share/frontier-orchestration/ledger/`).

`doctor` runs `fo.py doctor` from the installed folder of this package version.

## Security

- Every external command runs through Node's `execFile` or `spawn` with an argument array. No
  shell runs. The installer quotes nothing by hand. The quotes in the printed commands are for
  display only.
- The installer prints each command before it runs it.
- The installer refuses to run as root.
- The installer finds CLIs only in absolute `PATH` folders. It ignores relative `PATH` entries.
- The bundle copy refuses symbolic links.
- The installer asks before it changes something. `--yes` skips the question. `--dry-run`
  changes nothing.

## The bundle

`scripts/bundle.mjs` copies the plugin layout from the repository root into `npm/bundle/`:
`.claude-plugin/`, `.codex-plugin/`, `.agents/`, `.antigravity-plugin/`,
`skills/frontier-orchestration/`, `commands/`, `assets/`, `LICENSE` and `PRIVACY.md`. It skips
`__pycache__`, `*.pyc`, `tests` and `.DS_Store`. `assets/` is in the bundle because
`.antigravity-plugin/plugin.json` names `assets/logo.svg`.

The script also checks that the version in `npm/package.json` is the same as the version in each
plugin manifest, and in each `plugins[].version` of `.claude-plugin/marketplace.json`. The
install folder has the version in its name, so these versions must agree.

The script builds the bundle in a temporary folder, then renames it into place. If that rename
fails, the script puts the earlier bundle back.

Each bundle holds a marker file, `.fo-bundle`. `--out <folder>` sets another output folder. The
script refuses the output folder, changes nothing and exits 2 when the folder is one of these:

- the repository root, the `npm/` folder, or a parent of one of them (for example `--out .` or
  `--out ..` in `npm/`);
- a source item of the bundle, a parent of one, or a folder inside one;
- a symbolic link;
- a path that exists and is not a folder with the `.fo-bundle` marker. The script replaces only
  a bundle that it wrote. A `npm/bundle/` from a build before the marker has no marker: delete
  it once, then build again.

The script compares real paths (symbolic links in the parent folders are resolved). Other
errors exit 1.

`npm pack` runs the script as its `prepack` step. The `files` list in `package.json` puts only
`bin/`, `lib/`, `bundle/` and `README.md` in the tarball. `npm/bundle/` is git-ignored.

## Compared with the install paths of today

| Path | What it installs | Hosts | Setup |
| --- | --- | --- | --- |
| Plugin marketplaces (root `README.md`) | The full plugin, from GitHub | One command pair for each host. Antigravity needs a `git clone` first. | You run `fo.py install` or `/frontier-orchestration:setup` yourself. |
| skills.sh (`npx skills add bebecap/frontier-orchestration --skill frontier-orchestration`) | Only the skill folder | The agents that skills.sh supports | You run the setup yourself. |
| This prototype | The full plugin, from the npm tarball, into one stable local folder | Every host that it finds, in one run | It starts `fo.py install` for you. |

The plugin marketplaces get the plugin from GitHub. This prototype gets the plugin from the npm
tarball. The hosts then point at a local folder, so a later `git push` does not change the
installed plugin. A new version comes only from a new package version.

## Tests

Run in `npm/`:

```bash
npm test          # same as: node --test test/*.test.mjs
```

The tests need no network and no `npm install`. They put fake `claude`, `codex`, `agy` (and
sometimes `python3`) scripts on a temporary `PATH`. Each fake records its argv. Each test uses
its own temporary `HOME`. The tests check:

- the bundle contents;
- the atomic, versioned copy (a failed copy is simulated through a function seam);
- the exact argv for each host;
- that a failing host does not stop the others;
- that a host CLI that reads its answer from standard input gets end of input and stops at
  once;
- the record: only recorded hosts are removed, a failed removal gives exit 1 and keeps the
  plugin folder and the record;
- with stateful fake CLIs (they keep state and fail for a name that is not there, as the real
  CLIs do): an install that fails at `plugin install`, a plugin that the user removed by hand,
  and an install stopped with Ctrl-C between two hosts. In each case `uninstall` exits 0,
  deletes the folder, and leaves nothing registered;
- that `uninstall` without a record never deletes a version folder, that `--force` then
  works, that `--force` keeps the folder while a host still lists the plugin, and that
  `--force` with `--hosts` is refused (exit 2);
- that a list with entries but no known field runs no remove command, keeps the step in the
  record and the folder (exit 1), and that `--force` then drops the step from the record only;
  and the real `agy plugin list --json` format;
- ownership: a marketplace with the same name from another source, or of a source that the
  list cannot show, is not added or recorded; an agy plugin with the same name without the
  marker file, or one that the list cannot show, is not replaced or recorded, and `uninstall`
  leaves it; a Claude Code plugin with the same id whose marketplace is not listed stops all
  steps of that host; a `pending` step whose source cannot be found
  stays in the record and on the host (exit 1), and `--force` drops it from the record only; a `done` step that the user replaced with another source (Claude Code,
  Codex and agy) is left on the host; a marketplace that the user points at a folder without
  the marker file in the plugin folder is left on the host, with no remove command, and that
  folder stays; a marketplace of an earlier version of this installer (with the marker file) is
  removed;
- that a removal that cannot be checked (the remove command exits 1, or exits 0) keeps the step
  in the record and the folder (exit 1) in each run, and that `--force` drops it from the
  record and names the remove command;
- that leftovers of a stopped run are deleted (exact names only: `0.1.0` keeps the leftovers
  of `0.1.0-beta`), only when they have the marker file or are empty, and that no temporary
  copy is made in the skills folder;
- ownership: without a record, a Git checkout in the plugin folder stays and `uninstall` exits
  1 (also with `--force`); with a record, the version folder and the record go, but a file
  that you put in the plugin folder and the folder stay (exit 1, also with `--force`); a
  normal install and uninstall deletes the plugin folder (exit 0), and every recursive delete
  hits a folder with the marker file; `install` does not replace a version folder without the
  marker file (exit 1);
- that a failed bundle rename keeps the earlier bundle, and the marketplace version check;
- that the bundle script refuses `--out .`, `--out ..`, the repository root, a parent of it, a
  source folder, a folder inside a source folder, a symbolic link and a folder that it did not
  write (exit 2, nothing changed), in a copy of the repository in a temporary folder;
- that `--dry-run` changes nothing;
- the lock: while a real install runs (it waits in a fake `agy`), a second `install` or
  `uninstall` exits 1 at once and changes nothing; a lock file whose pid is gone is not taken
  over (exit 1, with its pid and start time); `--break-lock` deletes the lock file one time, and
  exits 1 when another run creates it first; on SIGTERM, a slow host command that ignores
  SIGTERM is killed, the lock file stays until that command is dead, and the late work of that
  command never happens; `install` and `uninstall --force` (also without the state folder) hold
  the lock while they ask, so another run cannot take it and nothing that it made is deleted;
- the timeout: a host command that ignores SIGTERM, or exits 0 on SIGTERM, is killed and gives
  a timeout failure (exit 124), never success;
- that `uninstall` keeps the config and the ledger;
- the root refusal (through a function seam, not as root);
- that paths with spaces and shell characters reach the hosts unchanged;
- end to end: `npm pack` (offline), unpack the tarball, run its bin in a temporary `HOME`.

## Before a publish

All of these are open:

1. Remove `"private": true` from `package.json`.
2. Find out if the npm name `frontier-orchestration` is free. If it is not, choose a scoped
   name (for example `@<scope>/frontier-orchestration`) and change the commands in this file.
3. Make the GitHub repository public. Add `repository`, `homepage` and `author` to
   `package.json`.
4. Turn on two-factor authentication for the npm account that publishes.
5. Publish from CI with provenance (`npm publish --provenance`). Provenance links the tarball to
   the commit and the workflow that built it. It needs a public repository.
6. Run the real `claude`, `codex` and `agy` CLIs against an installed folder on macOS and on
   Linux. The tests use fakes only.
7. Add the npx path to the root `README.md`.

## What is not done

- Nothing is published. The package is private.
- The tests use fake host CLIs. No test ran the real `claude`, `codex` or `agy` CLI.
- An upgrade is not proven. A new version gets a new folder, and `marketplace add` with the
  same marketplace name can fail. The installer reports that as a warning and goes on, so the
  host can still point at the old folder or at the GitHub source.
- A reinstall of the same version has a short moment between two renames when the folder does
  not exist.
- The lock is not taken over by itself. After a crash (for example SIGKILL or a power loss),
  the lock file stays, and each later run exits 1 until you run it with `--break-lock`.
  `--break-lock` trusts you: if you use it while another installer runs, two runs can work at
  the same time.
- On SIGINT or SIGTERM, the run stops only the process of the host command that it started (one
  pid). A process that the host command started itself can still run.
  A SIGKILL to the installer stops it at once: the host command and the lock file stay.
- A step whose owner `uninstall` cannot find stays in the record. If the host never lets the
  installer read its list again, only `uninstall --force` finishes the uninstall. The host then
  keeps that step, and it can point at the deleted folder. The output names the command that
  removes it.
- Windows is not supported. The skill needs macOS or Linux.
