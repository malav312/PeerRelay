# Native client setup

Create repeatable role-specific setup after pairing without changing global client
configuration, copying credentials, connecting a lease, or claiming that a running
chat has reloaded. Use a new private chat directory for either role; Alice's actual
workspace remains the relay's registered checkout.

Claude Code supports removing built-in tools with `--tools ""` and limiting MCP
configuration. Generate a dedicated launcher with those restrictions and the role
skill. Validate the required flags before launch; do not accept extra launch flags.
Native sign-in remains with the client. Test native startup separately from setup.

For Codex, generate MCP configuration and install the role skill, but report local
tool restrictions as unverified. Shell removal and read-only mode are insufficient
proof of a complete boundary. Do not silently launch an unrestricted Codex session.
Document the remaining external-isolation/native-policy checkpoint explicitly.

Setup refuses existing destinations, invalid roles, symlinked configuration, and
non-private credentials. Generated artifacts reference the existing credentials;
they never contain token values. Setup must not contact the remote relay.

Acceptance requires fresh native clients, inspection of effective tool inventory,
denial of local read/write/command attempts using disposable markers, successful
remote operations and question delivery, then explicit disconnect. This cannot be
established by installing files or asking a model whether it is restricted.
