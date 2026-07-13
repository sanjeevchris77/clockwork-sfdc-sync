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
import re
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

REP_NAMES = ["Sean Coughlin", "Greg Mark", "Abrahem Miya", "Chris Bowen"]  # NOTE: "Abrahem" matches the exact spelling of this Owner.Name in Salesforce
DEFAULT_OPP_AMOUNT = 200000

# RAISE 2025's directly-comparable in-person booth campaign (booth-only
# apples-to-apples comparison, per user direction -- NOT the full RAISE 2025
# event, which also has separate Sponsors/Attendees/Speakers/On-Site-Meetings
# /Virtual-Booth campaigns under the same "RAISE Summit 2025" parent).
RAISE_2025_BOOTH_CAMPAIGN_ID = "701TV00000SLsriYAD"  # Booth Visitors | Post-Conf | RAISE 2025

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
        try:
            with request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except error.HTTPError as e:
            print("SOQL query failed:", e.read().decode(), file=sys.stderr)
            print("Query was:", query, file=sys.stderr)
            raise
        records.extend(data["records"])
        next_url = data.get("nextRecordsUrl")
        url = f"{instance_url}{next_url}" if next_url else None
    return records


def domain_of(url):
    """Normalize a website URL down to a bare registrable-ish domain for
    matching Leads to Opportunities/Accounts, e.g.
    'https://www.Acme.com/about' -> 'acme.com'. Returns None for blank input."""
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^[a-z]+://", "", u)   # strip scheme
    u = re.sub(r"^www\.", "", u)       # strip leading www.
    u = u.split("/")[0]                # strip path
    u = u.split("?")[0].split("#")[0]  # strip query/fragment (belt & suspenders)
    u = u.split(":")[0]                # strip port
    return u or None


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
               Lead.OwnerId, Lead.Owner.Name, Lead.Company, Lead.Website,
               Lead.FirstName, Lead.LastName, Lead.Title, Lead.Email,
               Lead.MobilePhone, Lead.LeadSource, Lead.Status,
               Lead.Lead_Notes__c, Lead.LinkedIn__c
        FROM CampaignMember
        WHERE CampaignId IN ({campaign_id_list}) AND LeadId != null
    """
    members = soql(instance_url, token, cm_query)

    leads_out = []
    for m in members:
        lead = m.get("Lead") or {}
        notes = lead.get("Lead_Notes__c")
        status = lead.get("Status")
        leads_out.append({
            "campaign": (m.get("Campaign") or {}).get("Name"),
            "member_status": m.get("Status"),
            "lead_id": m.get("LeadId"),
            "owner": (lead.get("Owner") or {}).get("Name"),
            "company": lead.get("Company"),
            "website": lead.get("Website"),
            "domain": domain_of(lead.get("Website")),
            "first_name": lead.get("FirstName"),
            "last_name": lead.get("LastName"),
            "title": lead.get("Title"),
            "email": lead.get("Email"),
            "mobile": lead.get("MobilePhone"),
            "linkedin_url": lead.get("LinkedIn__c"),
            "lead_source": lead.get("LeadSource"),
            "notes": notes,
            "lifecycle_stage": classify_lifecycle(status, notes),
            "sfdc_link": f"{instance_url}/lightning/r/Lead/{m.get('LeadId')}/view",
        })

    lead_domains = {l["domain"] for l in leads_out if l.get("domain")}
    booth_scan_lead_ids = {l["lead_id"] for l in leads_out if l.get("lead_id")}

    def esc(s):
        return s.replace("\\", "\\\\").replace("'", "\\'")

    # --- Opportunities: pulled for ALL owners (not just the 4 reps) so we can
    # detect "this account already has SOME opportunity, even if a different
    # rep/team owns it" for the company-level engagement flag below. The
    # rep-specific pipeline list (opps_out) is then filtered down to just the
    # 4 reps from this same result set. Matched to booth-scan Leads by WEBSITE
    # DOMAIN rather than Account.Name (fuzzy text) or CampaignId (most Opps
    # won't be tagged with the campaign directly).
    opp_query = """
        SELECT Id, OwnerId, Owner.Name, AccountId, Account.Name, Account.Website,
               Amount, StageName, LeadSource, CampaignId, Campaign.Name,
               (SELECT Contact.Name, Contact.Title, Contact.Email FROM OpportunityContactRoles)
        FROM Opportunity
        WHERE Account.Website != null
    """
    all_opps = soql(instance_url, token, opp_query)

    opps_out = []
    company_engaged_via_opp = set()
    for o in all_opps:
        acct = o.get("Account") or {}
        opp_domain = domain_of(acct.get("Website"))
        if not opp_domain or opp_domain not in lead_domains:
            continue
        owner_name = (o.get("Owner") or {}).get("Name")
        company_engaged_via_opp.add(opp_domain)  # any owner counts as "already engaged"
        if owner_name in REP_NAMES:
            roles = (o.get("OpportunityContactRoles") or {}).get("records", [])
            opps_out.append({
                "opp_id": o.get("Id"),
                "owner": owner_name,
                "account": acct.get("Name"),
                "account_website": acct.get("Website"),
                "domain": opp_domain,
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

    # --- Contacts: any Contact already sitting on one of these Accounts is a
    # strong "someone here is already a known relationship" signal, regardless
    # of who owns the Account.
    contact_query = """
        SELECT Id, Name, Title, Email, AccountId, Account.Name, Account.Website
        FROM Contact
        WHERE Account.Website != null
    """
    contacts = soql(instance_url, token, contact_query)

    company_engaged_via_contact = set()
    for c in contacts:
        acct = c.get("Account") or {}
        d = domain_of(acct.get("Website"))
        if d and d in lead_domains:
            company_engaged_via_contact.add(d)

    # --- Other Leads: any OTHER open (unconverted) Lead at the same domain,
    # excluding the booth-scan Leads themselves, means someone else from that
    # company is already a separate active thread with us.
    company_engaged_via_other_lead = set()
    if booth_scan_lead_ids:
        exclude_ids = ",".join(f"'{esc(i)}'" for i in booth_scan_lead_ids)
        other_lead_query = f"""
            SELECT Id, Company, Website, Status, OwnerId, Owner.Name, CreatedDate
            FROM Lead
            WHERE Website != null AND IsConverted = false
                  AND Id NOT IN ({exclude_ids})
        """
        other_leads = soql(instance_url, token, other_lead_query)
        for ol in other_leads:
            d = domain_of(ol.get("Website"))
            if d and d in lead_domains:
                company_engaged_via_other_lead.add(d)

    company_already_engaged = (company_engaged_via_opp
                                | company_engaged_via_contact
                                | company_engaged_via_other_lead)

    # Stamp each booth-scan lead with the company-level engagement flag.
    for l in leads_out:
        d = l.get("domain")
        l["company_already_engaged"] = "Yes" if (d and d in company_already_engaged) else "No"

    # --- Account-level (ABM/ABX) rollup: one row per company seen at the
    # booth, tracking BOTH the breadth (how many leads/personas we're adding)
    # and the depth (funnel stage of each, plus whether the account already
    # had a relationship before RAISE).
    by_domain = {}
    for l in leads_out:
        d = l.get("domain") or f"__no_domain__:{l.get('company')}"
        by_domain.setdefault(d, []).append(l)

    account_summary = []
    for d, group in sorted(by_domain.items(), key=lambda kv: kv[0]):
        companies = {g.get("company") for g in group if g.get("company")}
        stage_counts = {}
        for g in group:
            stage = g.get("lifecycle_stage") or "Unknown"
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        account_summary.append({
            "domain": None if d.startswith("__no_domain__:") else d,
            "company": sorted(companies)[0] if companies else group[0].get("company"),
            "booth_scan_lead_count": len(group),
            "personas": [
                {"name": f"{g.get('first_name') or ''} {g.get('last_name') or ''}".strip(),
                 "title": g.get("title"),
                 "lifecycle_stage": g.get("lifecycle_stage"),
                 "owner": g.get("owner")}
                for g in group
            ],
            "lifecycle_stage_counts": stage_counts,
            "company_already_engaged": "Yes" if d in company_already_engaged else "No",
            "engaged_via_existing_opportunity": d in company_engaged_via_opp,
            "engaged_via_existing_contact": d in company_engaged_via_contact,
            "engaged_via_other_open_lead": d in company_engaged_via_other_lead,
            "existing_opportunities_all_owners": [
                {"owner": o["owner"], "stage": o["stage"], "amount": o["amount"]}
                for o in opps_out if o["domain"] == d
            ] if not d.startswith("__no_domain__:") else [],
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
    (OUT_DIR / "account_summary.json").write_text(json.dumps(account_summary, indent=2))
    (OUT_DIR / "meta.json").write_text(json.dumps({
        "campaign_ids": CAMPAIGN_IDS,
        "rep_names": REP_NAMES,
        "default_opp_amount": DEFAULT_OPP_AMOUNT,
        "lead_count": len(leads_out),
        "opp_count": len(opps_out),
        "distinct_companies": len(account_summary),
        "companies_already_engaged": sum(1 for a in account_summary if a["company_already_engaged"] == "Yes"),
        "companies_net_new": sum(1 for a in account_summary if a["company_already_engaged"] == "No"),
    }, indent=2))

    print(f"Wrote {len(leads_out)} leads, {len(opps_out)} rep-owned opportunities, "
          f"and {len(account_summary)} account rollups to {OUT_DIR}")

    # --- Discovery: find ALL Campaigns with "Raise" in the name (any year),
    # so we can identify the RAISE 2025 campaign(s) for a year-over-year
    # comparison without guessing IDs. This is read-only/exploratory --
    # doesn't affect the 2026 booth-scan outputs above.
    discovery_query = """
        SELECT Id, Name, StartDate, EndDate, Status, Type, NumberOfLeads,
               NumberOfConvertedLeads, NumberOfOpportunities, NumberOfWonOpportunities,
               AmountAllOpportunities, ParentId, Parent.Name
        FROM Campaign
        WHERE Name LIKE '%Raise%'
        ORDER BY StartDate ASC NULLS LAST
    """
    try:
        raise_campaigns = soql(instance_url, token, discovery_query)
    except error.HTTPError:
        raise_campaigns = []
        print("Campaign discovery query failed (non-fatal) -- see stderr above.", file=sys.stderr)

    campaigns_out = [{
        "id": c.get("Id"),
        "name": c.get("Name"),
        "start_date": c.get("StartDate"),
        "end_date": c.get("EndDate"),
        "status": c.get("Status"),
        "type": c.get("Type"),
        "number_of_leads": c.get("NumberOfLeads"),
        "number_of_converted_leads": c.get("NumberOfConvertedLeads"),
        "number_of_opportunities": c.get("NumberOfOpportunities"),
        "number_of_won_opportunities": c.get("NumberOfWonOpportunities"),
        "amount_all_opportunities": c.get("AmountAllOpportunities"),
        "parent_id": c.get("ParentId"),
        "parent_name": (c.get("Parent") or {}).get("Name"),
    } for c in raise_campaigns]

    (OUT_DIR / "campaigns_raise.json").write_text(json.dumps(campaigns_out, indent=2))
    print(f"Wrote {len(campaigns_out)} Raise-named campaigns (any year) to {OUT_DIR}/campaigns_raise.json")

    # --- RAISE 2025 vs RAISE 2026 comparison (booth-only, apples-to-apples,
    # per user direction -- NOT the full multi-campaign RAISE event for
    # either year). Pulls the 2025 in-person booth campaign the same way the
    # 2026 booth campaigns were pulled above, then compares lead/opportunity
    # counts, exact lifecycle-stage funnel (Lead/MEL/MQL/SAL/SQL/SQO -- real
    # Salesforce Status values, whatever they are), and a UNIFORM pipegen
    # potential of $200,000 per Opportunity for BOTH years (not real Amounts,
    # per explicit user direction, so the two years are compared on the same
    # yardstick). Also flags which specific leads (by email) and which
    # companies (by domain) attended/appear in BOTH years.
    cm_2025_query = f"""
        SELECT CampaignId, Campaign.Name, Status, LeadId,
               Lead.OwnerId, Lead.Owner.Name, Lead.Company, Lead.Website,
               Lead.FirstName, Lead.LastName, Lead.Title, Lead.Email,
               Lead.MobilePhone, Lead.LeadSource, Lead.Status,
               Lead.Lead_Notes__c, Lead.LinkedIn__c
        FROM CampaignMember
        WHERE CampaignId = '{RAISE_2025_BOOTH_CAMPAIGN_ID}' AND LeadId != null
    """
    members_2025 = soql(instance_url, token, cm_2025_query)

    leads_2025 = []
    for m in members_2025:
        lead = m.get("Lead") or {}
        notes = lead.get("Lead_Notes__c")
        status = lead.get("Status")
        leads_2025.append({
            "campaign": (m.get("Campaign") or {}).get("Name"),
            "lead_id": m.get("LeadId"),
            "owner": (lead.get("Owner") or {}).get("Name"),
            "company": lead.get("Company"),
            "website": lead.get("Website"),
            "domain": domain_of(lead.get("Website")),
            "first_name": lead.get("FirstName"),
            "last_name": lead.get("LastName"),
            "title": lead.get("Title"),
            "email": lead.get("Email"),
            "mobile": lead.get("MobilePhone"),
            "linkedin_url": lead.get("LinkedIn__c"),
            "lead_source": lead.get("LeadSource"),
            "notes": notes,
            "lifecycle_stage": classify_lifecycle(status, notes),
            "sfdc_link": f"{instance_url}/lightning/r/Lead/{m.get('LeadId')}/view",
        })

    domains_2025 = {l["domain"] for l in leads_2025 if l.get("domain")}

    # Re-use the already-fetched org-wide `all_opps` (Account.Website != null,
    # no year/campaign filter) and domain-match it against the 2025 booth
    # leads' companies, exactly as done for 2026 above -- the Campaign's own
    # NumberOfOpportunities rollup undercounts (same limitation observed on
    # the 2026 booth campaigns, which show 0 there despite 6 real domain-
    # matched Opportunities), so domain-matching is the consistent method
    # for BOTH years.
    opps_2025 = []
    for o in all_opps:
        acct = o.get("Account") or {}
        d = domain_of(acct.get("Website"))
        if d and d in domains_2025:
            opps_2025.append({
                "opp_id": o.get("Id"),
                "owner": (o.get("Owner") or {}).get("Name"),
                "account": acct.get("Name"),
                "domain": d,
                "amount": o.get("Amount"),
                "stage": o.get("StageName"),
            })

    def stage_funnel(lead_list):
        counts = {}
        for l in lead_list:
            s = l.get("lifecycle_stage") or "Unknown"
            counts[s] = counts.get(s, 0) + 1
        return counts

    emails_2025 = {(l.get("email") or "").strip().lower() for l in leads_2025 if l.get("email")}
    emails_2026 = {(l.get("email") or "").strip().lower() for l in leads_out if l.get("email")}
    overlap_emails = emails_2025 & emails_2026

    overlap_leads = {
        "2025": [l for l in leads_2025 if (l.get("email") or "").strip().lower() in overlap_emails],
        "2026": [l for l in leads_out if (l.get("email") or "").strip().lower() in overlap_emails],
    }

    overlap_domains = domains_2025 & lead_domains
    overlap_companies = sorted(overlap_domains)

    overlap_opps = {
        "2025": [o for o in opps_2025 if o["domain"] in overlap_domains],
        "2026": [{"opp_id": o["opp_id"], "owner": o["owner"], "account": o["account"],
                   "domain": o["domain"], "amount": o["amount"], "stage": o["stage"]}
                  for o in all_opps
                  if domain_of((o.get("Account") or {}).get("Website")) in overlap_domains],
    }

    comparison = {
        "methodology": ("Booth-only comparison: RAISE 2025 'Booth Visitors | Post-Conf' campaign "
                         "vs RAISE 2026's 3 'Booth Scans' campaigns. Opportunities matched to booth "
                         "leads by website domain (not CampaignId, which undercounts for both years). "
                         "Pipegen potential uses a UNIFORM $200,000 per Opportunity for both years "
                         "(not real Opportunity Amounts), per explicit request, so the two years are "
                         "compared on the same yardstick."),
        "raise_2025": {
            "campaign_id": RAISE_2025_BOOTH_CAMPAIGN_ID,
            "lead_count": len(leads_2025),
            "opportunity_count": len(opps_2025),
            "lifecycle_stage_counts": stage_funnel(leads_2025),
            "pipegen_potential": len(opps_2025) * DEFAULT_OPP_AMOUNT,
            "real_opportunity_amount_total": sum(o["amount"] or 0 for o in opps_2025),
        },
        "raise_2026": {
            "campaign_ids": CAMPAIGN_IDS,
            "lead_count": len(leads_out),
            "opportunity_count": len([o for o in all_opps
                                        if domain_of((o.get("Account") or {}).get("Website")) in lead_domains]),
            "lifecycle_stage_counts": stage_funnel(leads_out),
            "pipegen_potential": len([o for o in all_opps
                                        if domain_of((o.get("Account") or {}).get("Website")) in lead_domains]) * DEFAULT_OPP_AMOUNT,
            "real_opportunity_amount_total": sum(
                o.get("Amount") or 0 for o in all_opps
                if domain_of((o.get("Account") or {}).get("Website")) in lead_domains
            ),
        },
        "overlap": {
            "overlapping_lead_emails": sorted(overlap_emails),
            "overlapping_leads_2025": overlap_leads["2025"],
            "overlapping_leads_2026": overlap_leads["2026"],
            "overlapping_companies": overlap_companies,
            "overlapping_opportunities_2025": overlap_opps["2025"],
            "overlapping_opportunities_2026": overlap_opps["2026"],
        },
    }

    (OUT_DIR / "raise2025_booth_leads.json").write_text(json.dumps(leads_2025, indent=2))
    (OUT_DIR / "year_comparison.json").write_text(json.dumps(comparison, indent=2))
    print(f"Wrote {len(leads_2025)} RAISE 2025 booth leads and year_comparison.json to {OUT_DIR}")


if __name__ == "__main__":
    main()
