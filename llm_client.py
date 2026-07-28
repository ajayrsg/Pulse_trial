"""Shared Anthropic client construction.

Supports two credential modes, chosen by environment variable:

1. API key (recommended, simplest): set ANTHROPIC_API_KEY=sk-ant-api03-...
   The SDK sends it as the x-api-key header.

2. OAuth bearer token: set ANTHROPIC_AUTH_TOKEN=<token authorized for API use>.
   The SDK sends it as `Authorization: Bearer <token>`, and we add the
   standard `anthropic-beta: oauth-2025-04-20` header the API expects for
   OAuth tokens.

   The token MUST be one your organization legitimately provisions for API
   access (e.g. minted by `ant auth login`, or issued via your org's SSO /
   Console). This does NOT read Claude Code's own credentials and does NOT
   alter your prompts to impersonate another product — supply the token
   yourself via the env var.

If neither is set, a bare Anthropic() is returned, which falls back to the
SDK's own resolution order (env, then any `ant auth login` profile on disk).
"""

import os

from anthropic import Anthropic

OAUTH_BETA_HEADER = "oauth-2025-04-20"


def make_client():
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if auth_token:
        return Anthropic(
            auth_token=auth_token,
            default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
        )
    if api_key:
        return Anthropic(api_key=api_key)

    # Nothing explicit set — let the SDK resolve (profile on disk, etc.)
    return Anthropic()
