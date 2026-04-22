# Resetting a locked-out account

Users who fail sign-in five times in a row get a fifteen-minute lockout. Agents can cut that short from the admin console — the lockout counter is separate from the password itself, so a reset does not force the user to pick a new one.

## Clearing the lockout counter

Open Admin → Users, search by email, and click "Clear lockout." The user can sign in immediately with their existing password. If they have forgotten it, walk them through the self-service reset link below instead.

## Sending a password reset link

The self-service link lives at the bottom of every sign-in page and mails a one-time URL that expires after sixty minutes. The user must click the link from the same browser they used on the sign-in page — we bind it to the session cookie to prevent phishing replays.

## When SSO is the auth path

If the tenant is on SSO, we do not own the password. Clearing the lockout is still useful (it resets our internal counter) but the password itself is managed by the customer's IdP. Point them at their IT team.
