# Security policy

## Supported versions

Security fixes target the latest version of this integration on the default
`main` branch. Older versions do not receive backported security fixes.
See the [README](README.md) for Home Assistant requirements and upgrade
instructions.

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting form](https://github.com/f0x052/ha-odectyvodomeru/security/advisories/new),
also available under **Security > Advisories > Report a vulnerability**.

Do not disclose vulnerabilities or exploit details in public issues or pull
requests. Keep reports private until a fix and disclosure can be coordinated.
Ordinary bugs and feature requests can be reported through public issues, with
sensitive information removed.

Please include:

- The integration version and Home Assistant version.
- A description of the affected functionality and potential security impact.
- Minimal reproduction steps using synthetic data where possible.
- Relevant, redacted error messages and any suggested fix.

Never include passwords, access tokens, Home Assistant configuration backups,
unredacted HAR files, or personal account/location details, even in a private
report. Remove names, email addresses, physical addresses, flat numbers, meter
identifiers, and other identifying details from logs and screenshots.
If a credential has already been exposed, revoke or rotate it through the
relevant service.

## Scope and safe testing

This policy covers the integration code and its handling of credentials,
API responses, and Home Assistant entities. This is an unofficial integration;
it does not operate the odectyvodomeru.cz portal or its API.

Report vulnerabilities in Home Assistant or the portal directly to their
respective maintainers through their security reporting channels. If the
affected component is unclear, describe the issue privately here first.

Test only systems and accounts you own or have explicit permission to assess.
Do not access other households' data, attempt credential guessing, or disrupt
the portal or other users.

## Response and disclosure

Reports are handled on a best-effort basis by the project maintainer. There is
no guaranteed response or remediation deadline. The maintainer will coordinate
any necessary follow-up, fix, and public disclosure through the private report.
