import os

# Set required env vars BEFORE any app.config import happens, so Settings() doesn't
# fail at collection time in CI (no real secrets needed for tests — see docs/rule.md
# Secrets rule: these are dummy values, never real keys).
os.environ.setdefault("NVIDIA_API_KEY", "test-key-not-real")
os.environ.setdefault("NIM_MODEL_SMALL", "test/small-model")
os.environ.setdefault("NIM_MODEL_LARGE", "test/large-model")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("API_KEYS", "test-client-key")
