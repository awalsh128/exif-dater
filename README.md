# EXIF Dater

Date a folder of scanned photos in batch: it proposes dates, you approve them, and it writes them into
copies of the photos. Your originals are never changed.

## First-time setup

1. Install [Node.js](https://nodejs.org) 20 or later and [uv](https://docs.astral.sh/uv/). uv sets up
   Python and the app's dependencies on the first run.
2. Install [ImageMagick](https://imagemagick.org) (version 7, with the `magick` command) and
   [ExifTool](https://exiftool.org). On Ubuntu or Debian:

   ```sh
   sudo apt install imagemagick libimage-exiftool-perl
   magick -version   # older distributions package ImageMagick 6, which lacks this command
   ```

3. Get a [Claude API](https://platform.claude.com/docs/en/api/overview) key and set it in your shell:

   ```sh
   export ANTHROPIC_API_KEY=...
   ```

4. Clone this repository.

> [!IMPORTANT]
> Run the app by path (`npx .`) from the cloned folder. It isn't published to npm, so a bare
> `npx exif-dater` asks the public registry for that name.

## Date your photos

```sh
npx . ~/Pictures/scans
```

This opens a page in your browser. It handles `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp` and `.heic`
files, including those in subfolders.

1. Choose **Analyze new photos**. It shows the estimated cost and waits for you to choose **Send** before any
   photo goes to the API. Each photo is sent with its path inside the folder.
2. Check each group of photos, least certain first. Fix the date (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`), add a
   description if you like, and tick **Approve**. **Move to own group** separates a photo that doesn't belong.
3. Choose **Write approved photos**. Dated copies appear in `dated/` inside your photo folder.

Press Ctrl+C in the terminal when you're done. Progress is saved in `.exif-dater.json` in the photo folder,
so you can stop and come back.

## Get better dates for less

- **Name what you know.** A file or folder name starting with a date, such as `1987 beach` or `1987-06-14`,
  dates its photos without sending them to the API. Dates within the last year are ignored as likely scan
  dates.
- **Add notes.** Put facts that help, such as birth years, moves or trips, in a text file and pass it with
  `--notes notes.txt`. The notes are sent to the API with your photos.

## What gets written

The copies get the date at noon, one second apart within a group to keep their order. A missing month or day
becomes the first one, and the photo gets the keyword `date-estimated`. The time zone offset is `-08:00`
unless you pass `--tz`, for example `--tz -05:00`.

To get the new dates into Google Photos, upload the copies from `dated/`. Its API can't change the date of
photos already there, so remove the old uploads yourself.

## Choose a model

The default is `claude-haiku-5-5`, the cheapest. Its accuracy on real scans hasn't been measured, so test it
on at least 20 photos whose dates you know. List them in `truth.csv` in the photo folder, with paths relative
to that folder:

```csv
file,date
beach.jpg,1985-07
holidays/tree.jpg,1996
```

Then open **Model quality** on the page, or run:

```sh
npx . evaluate ~/Pictures/scans
```

It reports how close each model gets and what it costs, and recommends the cheapest one that meets your target
(by default, 80% of photos within 2 years). File names are hidden from the models. Without `truth.csv`, your
approved dates are used, which favors the model that proposed them.

Rough API cost per 100 photos, from prices as of 2026-11-03 (grouping them adds a little):

| Model               | Estimate |
| ------------------- | -------- |
| `claude-haiku-5-5`  | $0.06    |
| `claude-sonnet-5-5` | $1.10    |
| `claude-opus-5-5`   | $2.20    |
| `claude-fable-5-2`  | $4.40    |

## Use the terminal

```sh
npx . analyze ~/Pictures/scans --model claude-sonnet-5-5
npx . evaluate ~/Pictures/scans --report report.json
npx . analyze --help
```

`analyze` and `evaluate` ask before sending photos; `--yes` skips the question. Add `--no-open` to start the
page without opening a browser.

## Develop

See [AGENTS.md](AGENTS.md) for the layout, test commands and design rules.
