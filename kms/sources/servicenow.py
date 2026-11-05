"""ServiceNow client - Phase 2 placeholder. Mirrors kms.sources.atlassian:
document(id) -> dict with doc_id/title/url/updated_at/classification/checksum/acl/text,
fetch(id, user_token) for the live confirm, changed_since(ts) for polling."""


class ServiceNow:
    source = "servicenow"

    def __init__(self, *_, **__):
        raise NotImplementedError("ServiceNow is onboarded in Phase 2")
