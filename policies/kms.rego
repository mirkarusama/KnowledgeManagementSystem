# Attribute-based access rules for the KM platform (Open Policy Agent, Rego v1).
#
# Plain English: every user starts with "internal" clearance. Membership of
# certain groups raises it. A document can only be shown if its classification
# label is at or below the user's clearance, on top of the source system's own
# permissions (which are enforced separately by the index filter and the live
# confirm read).
package kms

import rego.v1

levels := {
	"public": 0,
	"internal": 1,
	"confidential": 2,
	"highly_confidential": 3,
	"restricted": 4,
}

# Which groups unlock which clearance. Edit this table (it is versioned in Git).
group_clearance := {
	"hr": 2,
	"confidential-readers": 2,
	"sre": 2,
	"security-team": 3,
	"restricted-access": 4,
}

default_level := 1

user_level := max({default_level} | {lvl |
	some g in input.user.groups
	lvl := group_clearance[g]
})

# Labels this user may see; used as an index filter (FIND step).
allowed_labels := [label |
	some label, lvl in levels
	lvl <= user_level
]

# Per-result decision; every decision is written to the audit log.
decision(r) := {"id": r.id, "allow": false, "reason": "unknown_classification"} if {
	not levels[r.classification]
} else := {"id": r.id, "allow": false, "reason": "classification_above_clearance"} if {
	levels[r.classification] > user_level
} else := {"id": r.id, "allow": false, "reason": "restricted_requires_restricted_access_group"} if {
	r.classification == "restricted"
	not "restricted-access" in input.user.groups
} else := {"id": r.id, "allow": true, "reason": "ok"}

decisions := [decision(r) | some r in input.resources]
