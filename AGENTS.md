# exif-dater

Dates scanned physical photos in batch, so the owner spends less time than reviewing them one by one.

## Specs

- Propose a date for each photo, at the precision the evidence supports.
- A person approves every date before it is written.
- Originals stay unchanged; dated copies go to `<photo dir>/dated/`.
- The input is a directory the user names.

## Cost

**Aim for the lowest total cost that still meets the specs.** Total cost counts API spend, the user's
time, setup and code to maintain.

Date a photo with the first of these that works:

1. An existing tool or plain code: the file or folder name, metadata, `exiftool`, ImageMagick.
2. One schema-constrained request per photo to the cheapest model that is accurate enough.
3. A stronger model, only where a comparison on photos with known dates shows the cheaper one falls short.

Prefer 1 over model analysis whenever it gives an acceptable answer. Keep model use to single requests
with code deciding what happens next; no agent loops. Before adding a model call, say why no existing tool
does the job and what it costs per 100 photos.

Choose a model with `evaluate`, which reports the cheapest one meeting the accuracy target. The default
(`DEFAULT_MODEL` in `exif_dater/claude.py`) has not yet been measured on real scans. Prices are copied into
`exif_dater/models.json`; update them and `prices_as_of` together.

## Commands

```sh
./setup.sh                 # interactive: tools and Claude sign-in, for the user to run
uv sync
uv run pytest              # exiftool tests skip when it isn't installed
npm test                   # the npx launcher
ruff check . && ruff format --check .
npx . <photo dir>          # web page: analyze, review, write, test models
npx . analyze <photo dir>
npx . evaluate <photo dir> # compare models on photos with known dates
```

Every command needs `uv` and ImageMagick (`magick`); the web page also needs `exiftool`.

## Layout

`bin/exif-dater.js` only launches: app logic and the page (`exif_dater/web/`) live in the Python package,
once. `package.json` `files` lists what `npx` installs, so add new runtime files there.

## Gotchas

- A scan's file and EXIF timestamps are the scan date, so they are not evidence.
- `analyze` and `evaluate` send photos to the Anthropic API and bill the user: run them on real photos only
  when the user asks. Tests fake the API.
- The help panel (`diagnose.py`) is plain code and free, except its credit test: no free endpoint says whether
  the account can pay, so that button sends a one-token request (under a cent, not per photo).
- A billing problem or rejected sign-in stops a run at the first one. The low-credit error is only
  recognizable by its message (`billing_problem` in `claude.py`).
- `evaluate` hides file names from the model, since a name may spell out the answer.
- The package is private and unpublished. Run it by path (`npx .`): a bare `npx exif-dater` asks the public
  npm registry for that name.
- Google Photos' API can't change the date of existing items, so new dates reach it by uploading the
  dated copies.
- `setup.sh` comes from a wizard template: edit below its `STAGES` marker and leave the library above it.
  The key file it writes is read by `bin/exif-dater.js`, so change the path in both.
- `update.sh` and `find_duplicates.sh` are the owner's earlier shell scripts, kept apart from the Python
  package.
