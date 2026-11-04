# Security

Report a vulnerability privately through GitHub: the repository's **Security** tab → **Report a vulnerability**. Please do not open a public issue.

In scope: anything that lets Uptick run a command you did not confirm, run a different command than the confirm step shows, or pass outside text (a version, a name, an id) into a command line or onto your terminal unfiltered. Also in scope: Uptick or its checker reading or sending data beyond what the README's "What it runs and sends" lists.

Out of scope: the behaviour of the tools Uptick runs once you confirm (Homebrew, the skills CLI, `claude plugin update`, `uv tool upgrade`).
