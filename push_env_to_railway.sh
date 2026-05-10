#!/bin/bash
# Push all .env vars to Railway (skips comments, blanks, SQLite/localhost overrides)
# Usage: bash push_env_to_railway.sh

set -e
cd "$(dirname "$0")"

echo "==> Pushing environment variables to Railway..."

while IFS= read -r line; do
  # Skip comments and blank lines
  [[ "$line" =~ ^#.*$ ]] && continue
  [[ -z "$line" ]] && continue

  key="${line%%=*}"
  val="${line#*=}"

  # Skip empty values (placeholders like sk_test_... or ls_...)
  [[ "$val" == "sk_test_..." ]] && continue
  [[ "$val" == "whsec_..." ]] && continue
  [[ "$val" == "price_..." ]] && continue
  [[ "$val" == "ls_..." ]] && continue
  [[ "$val" == "https://...@sentry.io/..." ]] && continue
  [[ -z "$val" ]] && continue

  # Skip DATABASE_URL and REDIS_URL — Railway injects these automatically
  [[ "$key" == "DATABASE_URL" ]] && continue
  [[ "$key" == "REDIS_URL" ]] && continue

  # Skip localhost-only redirect URIs — must be updated to Railway domain after deploy
  [[ "$val" == *"localhost"* && "$key" == *"REDIRECT_URI"* ]] && continue

  # Switch to production mode
  [[ "$key" == "APP_ENV" ]] && val="production"

  echo "  Setting $key"
  railway variables set "$key=$val"

done < .env

echo ""
echo "==> Done! Remember to update these AFTER deploy (use your Railway domain):"
echo "    GOOGLE_REDIRECT_URI=https://<your-app>.up.railway.app/api/v1/auth/google/callback"
echo "    MICROSOFT_REDIRECT_URI=https://<your-app>.up.railway.app/api/v1/auth/microsoft/callback"
echo "    APP_CORS_ORIGINS=[\"https://<your-app>.up.railway.app\"]"
