"""Runtime configuration, read from environment variables. Secrets are never stored in code."""
import os


class ConfigError(RuntimeError):
    pass


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
    return {
        "PASSWORD": password,
        "SECRET_KEY": secret,
        "PORT": port,
        "DB_PATH": env.get("FINANCE_DB", "data/finance.db"),
        "HOST": env.get("FINANCE_HOST", "0.0.0.0"),
    }
