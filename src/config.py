"""Runtime configuration, read from environment variables. Secrets are never stored in code."""
import os
import urllib.parse


class ConfigError(RuntimeError):
    pass


def validate_redirect_uri(uri: str) -> None:
    parts = urllib.parse.urlsplit(uri)
    local = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
    if not (parts.scheme == "https" and parts.hostname) and not local:
        raise ConfigError("GOOGLE_REDIRECT_URI must be an https:// address")


def load(env=None):
    """Return the config dict. Raises ConfigError when a required secret is missing."""
    env = os.environ if env is None else env
    password = env.get("FINANCE_PASSWORD", "")
    secret = env.get("SECRET_KEY", "")
    if not password:
        raise ConfigError("FINANCE_PASSWORD is not set")
    if len(secret) < 32:
        raise ConfigError("SECRET_KEY must be set and at least 32 characters")
    try:
        port = int(env.get("FINANCE_PORT", "8080"))
    except ValueError:
        raise ConfigError("FINANCE_PORT must be a number")
    if not 1 <= port <= 65535:
        raise ConfigError("FINANCE_PORT must be 1-65535")
    google = {k: env.get(k, "").strip() for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")}
    if any(google.values()) and not all(google.values()):
        raise ConfigError("Set all of GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI, or none")
    uri = google["GOOGLE_REDIRECT_URI"]
    if uri:
        validate_redirect_uri(uri)
    return {
        **google,
        "PASSWORD": password,
        "SECRET_KEY": secret,
        "PORT": port,
        "DB_PATH": env.get("FINANCE_DB", "data/finance.db"),
        "HOST": env.get("FINANCE_HOST", "0.0.0.0"),
    }
