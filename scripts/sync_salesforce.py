#!/usr/bin/env python3
"""
Sync Salesforce Leads / Opportunities / Campaign Members for the RAISE booth-scan
campaigns into flat JSON files under data/, so they can be read by other tools
(e.g. Claude via the GitHub connector) without ever needing direct Salesforce API
access.

Auth: OAuth 2.0 "Client Credentials" flow, via a Salesforce External Client
App (the newer replacement for classic Connected Apps). This flow only needs
a Client ID + Client Secret -- no username, password, or security token.
Credentials are read ONLY from environment variables (populated by GitHub
Actions secrets) -- never hardcoded, never logged.

Required env vars:
  SF_LOGIN_URL       your org's My Domain login URL, e.g.
                      https://yourorg.my.salesforce.com
                      (use the sandbox My Domain URL for a sandbox org)
  SF_CLIENT_ID       External Client App Consumer Key
  SF_CLIENT_SECRET   External Client App Consumer Secret

Note: Client Credentials Flow authenticates as whatever "Run As" user is
configured under the External Client App's Policies tab -> Client
Credentials Flow section -- that user's permissions determine what data this
script can see, so make sure it's a user with access to the relevant
Leads/Opportunities. SF_LOGIN_URL must be the org's specific My Domain URL
for this flow (the generic https://login.salesforce.com will not work for
Client Credentials Flow).
"""
import json
import os
import sys
from pathlib import Path
from urllib import request, parse, error

SF_LOGIN_URL = os.environ["SF_LOGIN_URL"]
CLIENT_ID = os.environ["SF_CLIENT_ID"]
CLIENT_SECRET = os.environ["SF_CLIENT_SECRET"]

# The 3 RAISE 2026 Booth Scan campaigns (Paris)
CAMPAIGN_IDS = [
    "701TV00000oNI18YAG",  # Booth Scans | 07-07-2026
    "701TV00000o71ibYAA",  # Booth Scans | 07-08-2026
    "701TV00000o6wcTYAQ",  # Booth Scans | 07-09-2026
]

REP_NAMES = ["Sean Coughlin", "Greg Mark", "Abraham Miya", "Chris Bowen"]
DEFAULT_OPP_AMOUNT = 200000

OUT_DIR = Path(__file__).resolve().parent.parent / "data"


def get_access_token():
    url = f"{SF_LOGIN_URL}/services/oauth2/token"
    payload = parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).encode()
    req = request.Request(url, data=payload, method="POST")
    try:
        with request.urlopen(req) as resp:
            data = json.loads(resp.read())
            return data["access_token"], data["instance_url"]
    except error.HTTPError as e:
        print("Auth failed:", e.read().decode(), file=sys.stderr)
        raise


def soql(instance_url, token, query):
    records = []
    url = f"{instance_url}/services/data/v60.0/query/?q={parse.quote(query)}"
    while url:
        req = request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with request.urlopen(req) as resp:
            data = json.loads(resp.read())
            records.extend(data["records"])
            next_url = data.get("nextRecordsUrl")
            url = f"{instance_url}{next_url}" if next_url else None
    return records


def classify_lifecycle(status, notes):
    """Best-effort MQL/MEL classification when Status doesn't already reflect it.
    Falls back to reading free-text Notes for qualification signals.
    Treat this as a starting point -- review before relying on it."""
    if status and status.lower() not in ("lead", "open", "new", ""):
        return status
    text = (notes or "").lower()
    qualified_signals = ["interested", "follow up", "follow-up", "demo", "budget",
                          "pilot", "poc", "evaluating", "buying", "timeline", "qualified"]
    if any(sig in text for sig in qualified_signals):
        return "Marketing Qualified Lead"
    return "Marketing Engaged Lead"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    token, instance_url = get_access_token()

    campaign_id_list = ",".join(f"'{c}'" for c in CAMPAIGN_IDS)

    # Campaign members -> Leads (booth scans are almost always Leads, not Contacts)
    cm_query = f"""
        SELECT CampaignId, Campaign.Name, Status, LeadId,
               Lead.OwnerId, Lead.Owner.Name, Lead.Company, Lead.FirstName,
               Lead.LastName, Lead.Title, Lead.Email, Lead.MobilePhone,
               Lead.LeadSource, Lead.Status, Lead.Description,
               Lead.LinkedIn_URL__c
        FROM CampaignMember
        WHERE CampaignId IN ({campaign_id_list}) AND LeadId != null
    """
    members = soql(instance_url, token, cm_query)

    leads_out = []
    for m in members:
        lead = m.get("Lead") or {}
        notes = lead.get("Description")
        status = lead.get("Status")
        leads_out.append({
            "campaign": (m.get("Campaign") or {}).get("Name"),
            "member_status": m.get("Status"),
            "lead_id": m.get("LeadId"),
            "owner": (lead.get("Owner") or {}).get("Name"),
            "company": lead.get("Company"),
            "first_name": lead.get("FirstName"),
            "last_name": lead.get("LastName"),
            "title": lead.get("Title"),
            "email": lead.get("Email"),
            "mobile": lead.get("MobilePhone"),
            "linkedin_url": lead.get("LinkedIn_URL__c"),
            "lead_source": lead.get("LeadSource"),
            "notes": notes,
            "lifecycle_stage": classify_lifecycle(status, notes),
            "sfdc_link": f"{instance_url}/lightning/r/Lead/{m.get('LeadId')}/view",
        })

    # Opportunities owned by the 4 reps, tied to the same campaigns (as primary
    # campaign source) -- adjust the WHERE clause if your org tracks Raise
    # differently (e.g. a custom Event__c field instead of CampaignId).
    owner_list = ",".join(f"'{n}'" for n in REP_NAMES)
    opp_query = f"""
        SELECT Id, OwnerId, Owner.Name, Account.Name, Amount, StageName,
               LeadSource, CampaignId, Campaign.Name,
               (SELECT Contact.Name, Contact.Title, Contact.Email FROM OpportunityContactRoles)
        FROM Opportunity
        WHERE CampaignId IN ({campaign_id_list}) AND Owner.Name IN ({owner_list})
    """
    opps = soql(instance_url, token, opp_query)

    opps_out = []
    for o in opps:
        roles = (o.get("OpportunityContactRoles") or {}).get("records", [])
        opps_out.append({
            "opp_id": o.get("Id"),
            "owner": (o.get("Owner") or {}).get("Name"),
            "account": (o.get("Account") or {}).get("Name"),
            "amount": o.get("Amount") or DEFAULT_OPP_AMOUNT,
            "stage": o.get("StageName"),
            "lead_source": o.get("LeadSource"),
            "campaign": (o.get("Campaign") or {}).get("Name"),
            "contacts": [
                {"name": (r.get("Contact") or {}).get("Name"),
                 "title": (r.get("Contact") or {}).get("Title"),
                 "email": (r.get("Contact") or {}).get("Email")}
                for r in roles
            ],
            "sfdc_link": f"{instance_url}/lightning/r/Opportunity/{o.get('Id')}/view",
        })

    # Rep-specific breakdown (leads + opps each rep owns)
    by_rep = {}
    for rep in REP_NAMES:
        by_rep[rep] = {
            "leads": [l for l in leads_out if l["owner"] == rep],
            "opportunities": [o for o in opps_out if o["owner"] == rep],
        }

    (OUT_DIR / "leads.json").write_text(json.dumps(leads_out, indent=2))
    (OUT_DIR / "opportunities.json").write_text(json.dumps(opps_out, indent=2))
    (OUT_DIR / "by_rep.json").write_text(json.dumps(by_rep, indent=2))
    (OUT_DIR / "meta.json").write_text(json.dumps({
        "campaign_ids": CAMPAIGN_IDS,
        "rep_names": REP_NAMES,
        "default_opp_amount": DEFAULT_OPP_AMOUNT,
        "lead_count": len(leads_out),
        "opp_count": len(opps_out),
    }, indent=2))

    print(f"Wrote {len(leads_out)} leads and {len(opps_out)} opportunities to {OUT_DIR}")


if __name__ == "__main__":
    main()
