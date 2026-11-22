# Clean main and existing clones

`main` contains the tested follow-through, security and container execution changes.
The spoken-code pairing PR is kept separate until its integration and acceptance
tests pass. The publishing connector and heartbeat recovery improvements are also
separate work.

The new `main` branch has sanitized ancestry without unrelated application references
or personal filesystem paths. Existing published feature branches still retain their
old history; rewriting those branches and PR ancestry requires separate explicit
approval. Required copyright and source attribution remain in
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
