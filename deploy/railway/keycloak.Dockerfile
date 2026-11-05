# Railway: Keycloak with the kms realm built in.
# KMS_WEB_URL (a Railway variable on this service, e.g. https://kms-web-production.up.railway.app)
# replaces http://localhost:3000 as the web app's allowed login return address.
FROM alpine:3.20 AS realm
ARG KMS_WEB_URL=http://localhost:3000
COPY keycloak/realm-kms.json /realm-kms.json
RUN sed -i "s#http://localhost:3000#${KMS_WEB_URL}#g" /realm-kms.json

FROM quay.io/keycloak/keycloak:26.2.5
COPY --from=realm /realm-kms.json /opt/keycloak/data/import/realm-kms.json
# Pilot only: dev mode keeps its data inside the container and re-imports the realm on each start.
CMD ["start-dev", "--import-realm"]
