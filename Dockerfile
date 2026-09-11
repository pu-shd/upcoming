# Image for pu-shd/upcoming.
#
# Two stages: `runtime` carries only what generating feeds needs, `dev` adds the test and
# quality tooling. The test and lint services both use `dev`, so the stage that runs pytest
# is the stage that installs it -- keeping `pytest` as the CMD of a stage without pytest is
# a container that fails at exec time with a confusing PATH error.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/New_York

WORKDIR /app

# Dependency metadata and the package skeleton first, so the install layer caches across
# edits to tests, config, and docs.
#
# tzdata is a declared dependency rather than a reliance on the base image: this image
# currently ships the system tz database, but that is not guaranteed across base-image
# revisions, and without one every ZoneInfo("America/New_York") lookup raises.
COPY pyproject.toml README.md ./
COPY upcoming ./upcoming
RUN pip install --no-cache-dir -e .

COPY . .

# No PYTHONPATH. The editable install plus pyproject's `pythonpath = ["."]` is the single
# mechanism; the predecessor had three overlapping ones (Dockerfile ENV, compose
# environment, and pytest rootdir inference) that could disagree with each other.
CMD ["python", "-m", "upcoming.cli", "check"]


FROM runtime AS dev

# Node is a test dependency, not a build one: the newsletter simulator's edition logic is
# JavaScript by decision, and the suite exercises it through Node rather than leaving it
# unchecked. It goes in `dev` alone -- generating feeds needs none of it -- and it is
# installed rather than made optional, because a test that skips itself when a tool is
# absent reports green while checking nothing.
RUN apt-get update \
 && apt-get install --no-install-recommends -y nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -e '.[dev]'
CMD ["pytest", "-q"]
