# Source identity after GitHub cleanup — 2026-11-16

The owner requested removal of coding-assistant instructions/settings/tasks
from GitHub while retaining local files for development. Cleanup used a separate
mirror after creating and verifying a local backup bundle. The working repository
was not reset/rewritten, local guides were not deleted, and historical ML locks
and references were not changed.

Twelve private paths were removed from 30 commits. Every old/new commit pair was
checked: `src`, `configs` and `sql` trees remained identical. Both writable branches
were updated atomically with explicit force-with-lease, without overwriting
unplanned remote changes. The local repository retaining original history was
not used for force-pushing.

| Historical revision | Corresponding public revision |
|---|---|
| `9237788f3e7a07f2b6661439e591dc37a869f480` — frozen Test evidence source | `2ed0134fa0a52fa4a6aa4b52719e5a70f7b7e817` |
| `d1997186f36a4131ec2ff133748c5641d1eb6a11` — documentation snapshot | `b9f85066c30b62d739ee8ff29aa631f8142b3642` |
| `3367ae6756bd1c2f0def9c7e9e564dd2bbca1ec2` — branch fix/complete-risklab | `d6acceead5293b3cc8870dc9b851af93ff4811dd` |
| `555c32cf65569d941a7087716ea1a7b63a29852e` — master before cleanup | `4617112b79fc4b629cfb8e870048f1cdc32f069c` |

Commit IDs changed because files and parent history were removed. The model
card/RESULTS retain original run/lock/source IDs; this table identifies matching
public source without replacing lock provenance. `verify_lock` still checks the
original commit identity; checks were not bypassed and locks were not edited to
accept new revisions. Audits requiring the original commit must use the original
local repository/backup. Locking a new model/release requires its own process and
authorization; Test is not automatically reopened.

## GitHub scope and limitations

Branch rewriting could not remove merged PR ref `refs/pull/1/head`, which retained
original ancestry. The owner authorized deleting/recreating the repository at
the same URL to replace its public graph after backup verification; local history
and guides were retained. Only the two cleaned branches were published, without
mirroring refs from the original Git repository. Two prototype research commits
remain in local backups; the public version uses one consolidated commit of the
current implementation/reports on a clean parent.

Verification requires a fresh clone, inventory of every advertised ref and a
public-path audit of every reachable commit tree, rather than the tip alone.
Repository settings and Pages source `master:/docs` were saved for restoration;
Actions were rerun. Rewriting was not used to change model evidence or open Test.

Publication snapshot `82eb52f03602ef6f7e8b1d01eed24fd362be5398` was verified
through a fresh mirror clone: 31 reachable commits, no private paths or PR refs,
and 360 public files matching local bytes. The new repository ID is `1459735047`;
the original PR SHA lookup returned HTTP 422. This is evidence for the new graph,
not certification of physical deletion from every GitHub system.

[GitHub documents PR-ref and cached-commit limitations](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
Auditing the new graph does not prove removal of every cache, internal GitHub
backup or third-party clone. The local pre-push guard prevents republishing
original private history; the public version is prepared in a separate cleaned
checkout.
