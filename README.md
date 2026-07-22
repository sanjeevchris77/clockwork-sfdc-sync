# clockwork-sfdc-sync

Scheduled GitHub Action that pulls an event's booth-scan Leads and rep-owned
Opportunities out of Salesforce and commits them as JSON into `data/`, so
they can be read by other tools (e.g. Claude via the GitHub connector)
without giving anyone direct Salesforce credentials.

The sync logic (`scripts/sync_salesforce.py`) is event-generic: everything
specific to one event (which Campaigns, which reps, the event's start date,
etc.) lives in a small config file under `events/<name>/config.json`. Adding
a new event -- a different conference, a different quarter, a different
booth -- means adding a new config file, not touching the script. See
"Adding a new event" below.

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

- `data/leads.json` -- every Lead campaign-member on the event's booth-scan
  campaigns, with owner, company, title, email, mobile, LinkedIn (if the
  `LinkedIn__c` field exists on Lead -- rename in the script if your org
  uses a different API name), Lead Source, Notes, and a best-effort
  MQL/MEL lifecycle classification when Status isn't already set.
- `data/opportunities.json` -- Opportunities on those campaigns owned by
  this event's tracked reps, with Amount (defaulting to the config's
  `default_opp_amount` if blank), Stage, Created Date, a Net New/Existing
  `origin` classification, and associated contact roles.
- `data/by_rep.json` -- the same data pre-split per rep.
- `data/account_summary.json` -- one row per company seen at the booth:
  breadth (persona count), depth (lifecycle stages), prior-engagement flags,
  potential pipeline, and ABM tier (if the config's `abm_tier_field` is set
  on Account).
- `data/meta.json` -- campaign IDs, rep names, and roll-up counts for a
  quick sanity check.
- `data/campaigns_raise.json`, `data/year_comparison.json`,
  `data/raise2025_booth_leads.json` -- year-over-year comparison against
  the config's `comparison_campaign_id`.

## Adding a new event

1. Copy `events/raise2026/config.json` to `events/<new-name>/config.json`
   and fill in the new event's values:
   - `campaign_ids` -- the Salesforce Campaign Ids for this event's
     booth-scan / venue-track campaigns
   - `rep_names` -- exact `Owner.Name` spelling for the reps whose pipeline
     should be tracked individually
   - `default_opp_amount` -- flat estimate used when a qualified account has
     no real open Opportunity yet
   - `qualified_stages` -- Lead Status values that count as "qualified"
   - `comparison_campaign_id` -- a prior event's campaign, for a
     year-over-year comparison (optional, but the script expects a value)
   - `event_start_date` -- this event's start date, used to separate
     genuinely new pipeline from pre-existing deals that happen to match a
     booth-scan account by domain
   - `net_new_lead_source` -- the exact Lead Source value reps tag on
     Opportunities sourced from this event
   - `abm_tier_field` -- the Account custom field holding this org's ABM
     tier classification, if any (ask the user for the exact API name --
     it varies per org and isn't guessable)
   - `ad_hoc_report_id` -- an optional saved Salesforce Report Id to pull
     as-is (set to `null` if not needed)
   - `output_dir` -- where this event's JSON files should be written, e.g.
     `events/<new-name>/data` (keep as `data` only for the original event,
     to stay backward-compatible with existing consumers of that path)
2. Point the GitHub Action at the new config by setting the `EVENT_CONFIG`
   env var (e.g. `events/<new-name>/config.json`) on the sync step in
   `.github/workflows/sync.yml` -- either edit that one workflow to run
   multiple events in sequence with different `EVENT_CONFIG` values, or add
   a second workflow file for the new event.
3. Run the workflow once manually to produce the first data files, same as
   the one-time setup above.
4. Build the dashboard from the new event's data files following the same
   chart/table conventions as the existing dashboard (KPI cards, engagement
   pie chart, persona funnel, Opportunity table, etc.) -- ask Claude to do
   this from the new `data/*.json` files; classification-heavy charts (like
   company segment or ABM tier) still need a human decision on categories
   the first time, same as they did here.

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
- All event-specific values now live in `events/<name>/config.json`, loaded
  via the `EVENT_CONFIG` env var (defaults to `events/raise2026/config.json`
  for backward compatibility). Nothing event-specific should need to change
  in `scripts/sync_salesforce.py` itself.
