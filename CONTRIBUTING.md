# Contributing

Thanks for helping. Fan Control drives the fans of real servers, so the bar is simple: a change must never
leave a server hotter than the BMC would have kept it.

## Reporting a problem

- **Something wrong with a server**: open the server's page, press **Diagnostics**, and attach the file to a
  [bug report](https://github.com/ST-DEVPT/rack-fan-control/issues/new?template=bug.yml). It holds what the
  BMC answers, without passwords, addresses or serial numbers. It is the one thing needed to fix hardware
  nobody here has.
- **Hardware that is not supported yet**: a
  [hardware report](https://github.com/ST-DEVPT/rack-fan-control/issues/new?template=hardware.yml) with the
  diagnostics file of that server is how new vendors get added.
- **A security problem**: privately, through a
  [security advisory](https://github.com/ST-DEVPT/rack-fan-control/security/advisories/new), never in an issue.

## Changing the code

```bash
git clone https://github.com/ST-DEVPT/rack-fan-control && cd rack-fan-control
python app.py                     # http://localhost:8080; add a "Demo server" to try it without hardware
python -m unittest                # the unit tests, a few seconds
ruff check .                      # the linter (pip install ruff)
```

No dependencies, no build step: Python 3.10 or newer and a browser. The browser tests need Playwright and a
running container (see [Development](README.md#development)); CI runs them on every push and pull request.

What makes a change easy to accept:

- **Safety stays first.** When the controller does not know something, the BMC decides. A new path that sends
  fan commands needs a test that shows what happens when its input is missing or wrong.
- **Vendor commands are only added when someone has run them** on that hardware; say which model and firmware
  in the pull request.
- **A test with every fix**, failing before it and passing after.
- **Every text on screen in English and Portuguese**: `web/i18n.js` (a test checks the pages).
- **Small pull requests**, one concern each, with a line in `CHANGELOG.md` for anything a user would notice.
- The code reads like the code around it: plain names, comments that say why, no new dependencies.

## Releases

Versions follow [semantic versioning](https://semver.org). A tag `vX.Y.Z` with its section in `CHANGELOG.md`
builds and publishes the image (`:X`, `:X.Y`, `:X.Y.Z`) and the GitHub release.
