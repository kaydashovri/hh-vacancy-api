# hh-bridge

A tiny bridge between GitHub Actions and the HeadHunter API.

## What it does

- searches HH over a rolling 14-day window;
- runs a basket of PM / Program / Delivery / AI / FinTech queries;
- deduplicates results by `vacancy_id`;
- downloads the full vacancy text;
- stores a public machine-readable index in `output/index.json`;
- stores each vacancy in `output/vacancies/<id>.json`;
- calculates a semantic hash so downstream consumers can detect meaningful vacancy changes.

No HH secrets are committed to the repository.

## 1. Create an HH developer application

Open the HeadHunter developer console:

https://dev.hh.ru/admin

Create an application and obtain an application access token.

HH API requests use:

- `Authorization: Bearer <access_token>`
- `HH-User-Agent: AppName/1.0 (your-email@example.com)`

The application token is generated once and can be rotated if compromised.

## 2. Add GitHub Actions secrets

Repository → Settings → Secrets and variables → Actions → New repository secret

Create:

### `HH_ACCESS_TOKEN`

Your HH application access token.

### `HH_USER_AGENT`

Example:

`RuslanHHBridge/1.0 (your-email@example.com)`

Do **not** put either value into source files, issues, commits or chat messages.

## 3. Allow Actions to write scan results

Repository → Settings → Actions → General → Workflow permissions

Enable **Read and write permissions** if your repository policy requires it.

The workflow also declares:

```yaml
permissions:
  contents: write
```

## 4. Run manually

Repository → Actions → **HH vacancy scan** → **Run workflow**

After the run finishes, inspect:

- `output/index.json`
- `output/vacancies/<vacancy_id>.json`

## 5. Automatic schedule

The workflow runs every day at:

- 13:45 UTC
- 16:45 Moscow time

This leaves time for a downstream ChatGPT task to read the fresh index after 17:00 Moscow time.

GitHub scheduled workflows can occasionally start a little late, so the downstream consumer should tolerate a short delay.

## Search geography

`config/queries.json` currently uses:

```json
"areas": ["1"]
```

HH area `1` is Moscow.

Change or remove the `areas` array if you want broader geography.

## Security

This repository may be public.

Safe to publish:
- Python code
- search queries
- vacancy IDs
- public vacancy descriptions
- generated indexes

Never publish:
- HH access token
- HH client secret
- GitHub personal access tokens
- email passwords

Keep those only in GitHub Secrets.
