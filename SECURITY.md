# Reporting a security problem

Please don't open a public issue for a security problem. Use **Security → Report a vulnerability** on this repository's page, which sends it to the maintainers privately.

Say what you found, how to reproduce it, and which version or commit you were on. You'll get an answer as soon as someone can read it. Nobody is paid to watch this inbox all day, so give it a few days before you chase.

Inrface holds people's private ideas and a GitHub token for each project, so these matter most: signing in without being allowed, reading another person's private text, starting a build nobody pressed Start build for, and anything that leaks a token.

Cloudflare Access is the recommended way to sign people in. The app's own sign-in (`docker-compose.builtin.yml`) is newer and hasn't been reviewed by anyone outside the project, so reports about it are especially welcome.
