# clockwork-sfdc-sync

Scheduled GitHub Action that pulls RAISE booth-scan Leads and rep-owned
Opportunities out of Salesforce and commits them as JSON into `data/`, so
they can be read by other tools (e.g. Claude via the GitHub connector)
without giving anyone direct Salesforce credentials.

## One-time setup

1. **Add repo secrets** (Settings -> Secrets and variables -> Actions -> New
   repository secret), using the "Clockwork SFDC Sync" External Client App
   and its Client Credentials Flow:
   - `SF_CLIENT_ID` -- External Client App Consumer Key
   - `SF_CLIENT_SECRET` -- External Client App Consumer Secret
   - `SF_LOGIN_URL` -- your org's My Domain URL, e.g.
     `https://yourorg.my.salesforce.com` (must be the specific My Domain,
     not the generic login.salesforce.com -- Client Credentials Flow
     requires it)

   None of these are ever seen by Claude or committed to the repo -- they
   live only in GitHub's encrypted secrets store and are injected as env
   vars at Action runtime.

   Before this works, the External Client App also needs a "Run As" user
   set under its Policies tab -> Client Credentials Flow section -- that
   user's permissions determine what Leads/Opportunities this script can
   see.

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
  (defaulting to $200,000 if blank), Stage, and associated contact roles.
- `data/by_rep.json` -- the same data pre-split per rep.
- `data/meta.json` -- campaign IDs, rep names, and counts for a quick sanity
  check.

## Notes / caveats

- Auth uses the OAuth 2.0 Client Credentials flow via a Salesforce External
  Client App -- simpler than the old Username-Password flow since it only
  needs a Client ID + Secret, but it authenticates as a specific "Run As"
  user configured on the app, so that user needs read access to the
  relevant Leads/Opportunities. If the Action fails with
  `invalid_client_id` or similar, double check `SF_LOGIN_URL` is the org's
  exact My Domain URL, and that "Enable Client Credentials Flow" is checked
  and saved on the app's Settings tab.
- The lifecycle classification (MQL vs MEL) from the Notes field is a
  simple keyword heuristic -- review it before trusting it fully. If your
  org already sets Lead Status accurately, that value is used as-is instead.
- Adjust `CAMPAIGN_IDS`, `REP_NAMES`, and `DEFAULT_OPP_AMOUNT` at the top of
  `scripts/sync_salesforce.py` any time these change.
