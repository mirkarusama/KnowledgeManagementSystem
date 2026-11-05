package kms_test

import rego.v1

import data.kms

test_default_internal if {
	kms.user_level == 1 with input as {"user": {"groups": []}}
}

test_hr_sees_confidential if {
	d := kms.decisions with input as {"user": {"groups": ["hr"]}, "resources": [{"id": "a", "classification": "confidential"}]}
	d[0].allow
}

test_engineer_blocked_from_confidential if {
	d := kms.decisions with input as {"user": {"groups": ["engineering"]}, "resources": [{"id": "a", "classification": "confidential"}]}
	not d[0].allow
	d[0].reason == "classification_above_clearance"
}

test_unknown_label_denied if {
	d := kms.decisions with input as {"user": {"groups": ["restricted-access"]}, "resources": [{"id": "a", "classification": "secret-ish"}]}
	not d[0].allow
}
