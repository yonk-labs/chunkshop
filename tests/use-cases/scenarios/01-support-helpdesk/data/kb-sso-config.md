# Configuring SAML SSO for a tenant

SSO is a per-tenant switch. Once enabled, local passwords are disabled for everyone in that tenant and all sign-ins flow through the customer's identity provider. Plan the cutover — users with stale sessions stay signed in until those sessions expire.

## Metadata exchange

The tenant admin uploads their IdP metadata XML in Admin → Security → SSO. We parse the entityID, the SingleSignOnService endpoint, and the signing certificate. Our matching ACS URL and entityID show up on the same page — hand those back to the IdP.

## Attribute mapping

We require three assertions: `NameID` as email, `firstName`, and `lastName`. Group membership is optional; if the IdP sends a `groups` attribute we can map it to roles, but the default is to leave every SSO user as a basic member until an admin promotes them.

## Common failures

The two failure modes we see most: clock skew beyond five minutes (check NTP on the IdP) and the IdP signing with a different certificate than the one in the metadata (rotate in Admin → Security → SSO and ask the customer to re-upload).
