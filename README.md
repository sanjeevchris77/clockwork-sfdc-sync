# clockwork-sfdc-sync

Scheduled GitHub Action that pulls RAISE booth-scan Leads and rep-owned
Opportunities out of Salesforce and commits them as JSON into `data/`, so
they can be read by other tools (e.g. Claude via the GitHub connector)
without giving anyone direct Salesforce credentials.

## One-time setup

1. **Add repo secrets** (Settings -> Secrets and variables -> Actions -> New
   repository secret). Use the Connected App / login you already have for
   your other Salesforce dashboards:
   - `SF_CLIENT_ID` -- Connected App Consumer Key
   - `SF_CLIENT_SECRET` -- Connected App Consumer Secret
   - `SF_USERNAME` -- Salesforce login username
   - `SF_PASSWORD` -- Salesforce login password
   - `SF_SECURITY_TOKEN` -- Salesforce security token
   - `SF_LOGIN_URL` -- optional, defaults to `https://login.salesforce.com`
     (use `https://test.salesforce.com` for a sandbox)

   None of these are ever seen by Claude or committed to the repo -- they
   live only in GitHub's encrypted secrets store and are injected as env
   vars at Action runtime.

2. **Run the workflow once manually**: go to the Actions tab -> "Sync
   Salesforce data" -> "Run workflow". This creates the first `data/*.json`
   files.

3. After that it re-runs automatically every 6 hours (edit the cron
   schedule in `.github/workflows/sync.yml` to change frequency), and can
   always be re-triggered manually.

## What gets synced

- `data/leads.json` -- every Lead campaign-member on the 3 RAISE booth-scan
  campaigns, with owner, company, title, email, mobile, LinkedIn (if the
  `LinkedIn_URL__c` field exists on Lead -- rename in the script if your org
  uses a different API name), Lead Source, Notes, and a best-effort
  MQL/MEL lifecycle classification when Status isn't already set.
- `data/opportunities.json` -- Opportunities on those campaigns owned by
  Sean Coughlin, Greg Mark, Abraham Miya, or Chris Bowen, with Amount
  (defaulting to $50,000 if blank), Stage, and associated contact roles.
- `data/by_rep.json` -- the same data pre-split per rep.
- `data/meta.json` -- campaign IDs, rep names, and counts for a quick sanity
  check.

## Notes / caveats

- Auth uses the OAuth 2.0 Username-Password flow, which Salesforce has been
  deprecating for newly created Connected Apps (existing ones, like the one
  you're reusing here, typically still work). If the Action fails with an
  `unsupported_grant_type` error, the org has this flow disabled and we'd
  need to switch to the JWT Bearer flow instead (different secrets: a
  private key instead of username/password).
- The lifecycle classification (MQL vs MEL) from the Notes field is a
  simple keyword heuristic -- review it before trusting it fully. If your
  org already sets Lead Status accurately, that value is used as-is instead.
- Adjust `CAMPAIGN_IDS`, `REP_NAMES`, and `DEFAULT_OPP_AMOUNT` at the top of
  `scripts/sync_salesforce.py` any time these change.
