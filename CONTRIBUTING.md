# Contributing

Thanks for looking. This widget is maintained by the City of Grand Junction GIS Division and
shared as is; issues and pull requests are welcome, and there is no obligation on either side.

## Reporting a problem

Open an issue with:

- the Experience Builder version (Developer Edition or the portal's build),
- the widget version (from `manifest.json`),
- what you did, what happened, and what you expected,
- the browser console output if anything was logged.

## Pull requests

- Keep a pull request to one change.
- Match the existing style: two space indent, no semicolons where the file does without them,
  and the same comment voice as the file you are editing.
- Update `CHANGELOG.md` and bump the version in both `manifest.json` and `package.json`.
- Build once with `npm start` in the Experience Builder `client` folder and exercise the widget
  before opening the pull request. `npx tsc -p .` in the widget folder should report no errors.

## Repository layout

The widget itself lives in the subfolder named after it. The repository root holds the README,
the license, and the publishing script; it is not part of the widget.