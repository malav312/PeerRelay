# Clean main and existing clones

`main` contains the tested follow-through, security and container execution changes.
The spoken-code pairing PR remains separate for two-laptop acceptance, with its
automated integration checks on the cleaned history. The publishing connector and heartbeat recovery improvements are also
separate work.

The published `main` and `feat/spoken-code-pairing` branches have sanitized ancestry
without unrelated application references or personal filesystem paths. The obsolete
`codex/requester-response` and `codex/follow-security` branches were removed with the
maintainer's approval. PR #1 targets `main` and remains unmerged. Required copyright
and source attribution remain in
`LICENSE` and `NOTICE`. The pairing PR's ordinary English code-word vocabulary is
not a project reference.

Existing clones still contain the old history. Save any uncommitted work and local
commits separately, then use a fresh clone in a new directory:

```sh
git clone https://github.com/malav312/PeerRelay.git PeerRelay-clean
cd PeerRelay-clean
git switch main
```

Do not merge old branches into the cleaned history or force-push them back. Transfer
necessary local changes as reviewed patches. A private rollback bundle was retained
by the maintainer; it must not be published.

Rewriting branch tips does not erase third-party clones, cached commit pages or
GitHub-managed pull-request references. No claim of complete remote erasure is made.
Historical verification records can mention commit IDs from before the rewrite.

The cleanup does not rotate pairing credentials, migrate journals or restart live
clients. Stop active sessions explicitly before changing their installed code.
