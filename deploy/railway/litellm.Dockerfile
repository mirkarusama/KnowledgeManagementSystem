# Railway: model gateway with its config files built in (Railway cannot mount files from a laptop).
# Pick the config with the LITELLM_CONFIG variable: config.demo.yaml | config.openai.yaml | config.ollama.yaml
# Files go in /kms-litellm, NOT /app/litellm (that is LiteLLM's own code inside this image).
FROM ghcr.io/berriai/litellm:main-v1.74.9-stable
COPY litellm/ /kms-litellm/
ENV LITELLM_CONFIG=config.demo.yaml
ENTRYPOINT ["/bin/sh", "-c", "exec litellm --config /kms-litellm/${LITELLM_CONFIG} --port 4000"]
CMD []
