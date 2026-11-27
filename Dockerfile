FROM node:22.14.0-bookworm-slim@sha256:1c18d9ab3af4585870b92e4dbc5cac5a0dc77dd13df1a5905cea89fc720eb05b AS codex-cli
RUN npm install --global --no-audit --no-fund @openai/codex@0.171.0-alpha.13.2 \
    && codex --version

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG RENDER_GIT_COMMIT=""
ARG VCS_REF=${RENDER_GIT_COMMIT}
LABEL org.opencontainers.image.revision=${VCS_REF}
COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && codex --version \
    && useradd --create-home --uid 10001 --user-group reviewer
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN if [ -n "$VCS_REF" ]; then \
        printf '%s\n' "$VCS_REF" | grep -Eq '^[0-9a-f]{40}$' && printf '%s\n' "$VCS_REF" > .image-revision; \
    fi
COPY requirements.txt .
RUN pip install --no-cache-dir --requirement requirements.txt
COPY reviewer/*.py reviewer/*.css reviewer/*.js reviewer/workbench.html reviewer/
COPY design/concepts/gallery.css design/concepts/case.js design/concepts/gallery.js design/concepts/index.html design/concepts/
COPY runs/bcc-final/review.json runs/bcc-final/review.html runs/bcc-final/
COPY runs/bcc-release/review.json runs/bcc-release/review.html runs/bcc-release/review.pdf runs/bcc-release/
COPY runs/bcc-release/owner/dialogue.json runs/bcc-release/owner/dialogue.html runs/bcc-release/owner/wrong-revision.html runs/bcc-release/owner/
COPY cloud/runtime.py cloud/public.py cloud/
COPY eval/gemini/behavior/bcc-release.html eval/gemini/behavior/bcc-release.json eval/gemini/behavior/
RUN chmod -R a+rX,go-w reviewer design runs cloud eval \
    && install -d -m 0700 -o reviewer -g reviewer /home/reviewer/.codex
USER reviewer
RUN test -r eval/gemini/behavior/bcc-release.html && test -r eval/gemini/behavior/bcc-release.json
EXPOSE 8765
CMD ["python", "-m", "reviewer.serve"]
