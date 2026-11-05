# Railway: OPA with the policy file built in.
FROM openpolicyagent/opa:1.4.2-static
COPY policies/kms.rego /policies/kms.rego
CMD ["run", "--server", "--addr=0.0.0.0:8181", "--set=decision_logs.console=true", "/policies/kms.rego"]
