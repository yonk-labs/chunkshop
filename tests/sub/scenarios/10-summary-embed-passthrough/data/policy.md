# Password policy

Passwords must be at least 12 characters and include uppercase, lowercase,
digit, and symbol. Reuse of the last 8 passwords is blocked. Accounts are
locked for 15 minutes after 5 failed login attempts.

# API key rotation

Service API keys rotate every 90 days automatically. Manual rotation is
available from the admin console for any key, and is required immediately
on team-member offboarding. Old keys remain valid for a 24-hour grace
window to allow in-flight requests to complete.
