# Findings

Back to the [knowledge map](../README.md). One line per lesson; each links to its own note in this folder.

## Findings
- [Google OAuth apps in Testing mode lose access after 7 days](finding-google-testing-mode-tokens-expire.md): Publish the OAuth consent screen (In production) or the refresh token stops working weekly.
- [Use an explicit OAuth redirect URI behind the https proxy](finding-redirect-uri-behind-proxy.md): url_for(_external=True) builds http:// URLs here, so GOOGLE_REDIRECT_URI is set explicitly in .env.
- [Code changes need a Docker rebuild](finding-docker-needs-rebuild.md): The image copies src/, so run docker compose up -d --build after every change or the old code keeps running.