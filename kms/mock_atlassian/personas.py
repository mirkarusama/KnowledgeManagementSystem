"""Demo people. The same users and groups exist in Keycloak (keycloak/realm-kms.json).

  alice  - engineer and on-call SRE         -> engineering docs, SRE-only pages, confidential clearance
  bob    - engineer                          -> engineering docs, but not SRE pages or confidential ones
  carol  - HR partner                        -> HR space and HR tickets, confidential clearance
  dave   - security engineer                 -> engineering + security-only tickets, highly-confidential clearance
  erin   - contractor on the Ops team        -> company-wide pages and the OPS project only
Everyone is in "all-staff".
"""

PERSONAS = {
    "alice": {"groups": ["all-staff", "engineering", "sre"], "account_id": "acc-alice", "email": "alice@example.com"},
    "bob": {"groups": ["all-staff", "engineering"], "account_id": "acc-bob", "email": "bob@example.com"},
    "carol": {"groups": ["all-staff", "hr"], "account_id": "acc-carol", "email": "carol@example.com"},
    "dave": {"groups": ["all-staff", "engineering", "security-team"], "account_id": "acc-dave", "email": "dave@example.com"},
    "erin": {"groups": ["all-staff", "contractors"], "account_id": "acc-erin", "email": "erin@example.com"},
}


def debug_header(username: str) -> str:
    p = PERSONAS[username]
    return f"{username};{','.join(p['groups'])};{p['account_id']}"
