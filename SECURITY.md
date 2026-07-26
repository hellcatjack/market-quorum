# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability, exposed credential,
authentication bypass, or report containing private assessment data. Use a
private GitHub Security Advisory for this repository instead:

1. Open the repository's **Security** tab.
2. Choose **Advisories** and **Report a vulnerability**.
3. Include the affected component, reproduction steps, impact, and a minimal
   proof of concept with every credential and personal value redacted.

If private vulnerability reporting is unavailable, contact
`hellcatjack@gmail.com` with the subject `MarketQuorum security report`. Do not
attach databases, `.env` files, raw Gateway audits, or assessment artifacts.

## Credential exposure

If a real API key, OAuth secret, database password, cookie secret, signing key,
or access token is committed or shared, treat it as compromised immediately:

1. Revoke or rotate the credential at its issuer.
2. Stop using affected backups and deployment bundles until they are checked.
3. Remove the value from every reachable Git object before publishing again.
4. Run the repository's secret scan and full verification gate.

Deleting a value in a later commit is not sufficient because Git preserves old
objects.

## Supported code

Security fixes target the current `main` branch. MarketQuorum is research
software and must not be treated as financial advice or as an autonomous order
execution system.
