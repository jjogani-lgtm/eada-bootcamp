# Romulus — Ames homes and price guide

Romulus is a small Ames, Iowa home marketplace with an in-browser price guide. It includes account registration, property listings, and an internal message inbox backed by SQLite. The prediction model and preprocessing settings are embedded in `index.html`; `model.json` is also kept as a separate copy.

## Run Romulus locally

Use Python 3.10 or newer. From this folder, run:

```sh
python3 server.py
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in Safari or another browser. Keep the server running while using accounts, listings, and messages. Opening `index.html` directly from Finder still runs the price calculator, but the marketplace needs the server. The interactive OpenStreetMap view also needs an internet connection.

The SQLite file is created at `romulus.sqlite3` the first time the server runs. Account passwords are stored as salted PBKDF2 hashes. Email addresses are not verified. Messages are saved in the Romulus inbox; the app does not send email notifications. Sellers may optionally choose to show a phone number publicly on a listing. Other contact details stay private until a buyer sends a message, at which point the seller sees the buyer’s account email to reply.

For local use, the server binds to `127.0.0.1`. It also reads the standard `PORT` environment variable and can bind to `0.0.0.0` for a hosted service. A Render deployment recipe is in the repository root at `render.yaml`; it uses HTTPS cookies and stores SQLite on a persistent disk. Render requires a paid web service for persistent disks, so creating that service will incur a charge. Before opening Romulus to the public, plan for database backups, account recovery, and abuse controls; this starter server does not yet include password reset, listing moderation, or request rate limits.

## Price guide and map

The underlying model uses 2,930 recorded Ames home sales from 2006–2010. Romulus offers two estimate types: the original model result with no later market adjustment, or an indicative 2026-dollar guide. The 2026 guide multiplies the original result by 324.78 / 166.78 (about 1.947), the Ames, IA metro all-transactions FHFA HPI ratio for 2026 Q2 versus 2010 Q2. This reflects broad metro price movement; it is not a retrain or a property-level valuation. It cannot account for a specific home's updates, current condition, or local differences.

Choose the neighborhood from the dropdown. The zoomable street map is provided by [OpenStreetMap](https://www.openstreetmap.org/copyright); neighborhood boundaries are not part of the model data and are not shown on the map.

The City of Ames publishes residential sale data through 2022, but its page notes that some property features such as area and year built reflect current records rather than conditions at the sale date. Romulus does not represent the old model as retrained on those sales. The displayed $14,621 average absolute error is the historical model comparison score, not a confidence interval and not a measure of 2026 accuracy.

The price model covers Ames-area detached homes, townhouses, duplexes and two-family homes. It does not contain Boston sales or apartment flats. The latest available index value included here is 2026 Q2; update the factor when FHFA releases a newer Ames MSA value.

Sources: [Ames residential sales](https://www.cityofames.org/My-Government/Departments/City-Assessor/Sales) · [FHFA Ames MSA index values](https://fred.stlouisfed.org/data/ATNHPIUS11180Q) · [FHFA HPI coverage and methodology](https://www.fhfa.gov/faqs/hpi?tab=coverage)

The five quality choices map to the model’s 1–10 overall quality scale. Inputs not shown in the form use median or common values from the training data. The historical model uses 2010 as its sale year; homes built after 2010 are treated as 2010-built by the existing input mapping.
