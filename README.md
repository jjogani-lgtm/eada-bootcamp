# Romulus

Romulus is an Ames, Iowa home marketplace with an interactive price guide, public listings, and buyer–seller messaging. The estimator offers the original 2006–2010 sales model and an indicative 2026-adjusted guide. It includes an interactive OpenStreetMap view.

## Run locally

From the `portal` directory, run `python3 server.py` and open `http://127.0.0.1:8000`. The app uses only Python's standard library. SQLite is created locally as `portal/romulus.sqlite3` and is intentionally excluded from this public repository.

## Deploy on Render

The root `render.yaml` describes the Render web service. Create a service from this repository using that Blueprint. It serves the site over HTTPS and stores SQLite at `/var/data/romulus.sqlite3` on a persistent disk. Render requires a paid web service for persistent disks. See `portal/README.md` for details on the model, data, and current account features.

## Public-use limitations

The model is based on Ames home sales from 2006–2010; the 2026 figure is an indicative metro-level index adjustment, not a retrained model or a property-specific appraisal. Accounts do not have email verification or password recovery, and the marketplace does not yet include listing moderation or request rate limits. Add operational safeguards and a backup plan before inviting broad public use.
