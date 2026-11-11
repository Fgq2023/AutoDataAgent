# M4 review corrections · 2026-11-11

Both review sources were checked. The separate pinned GitHub review reported a clean-clone rehearsal failure because outputs did not exist. The supplied local review identified five code issues and two documentation/evidence issues.

All eight items have been addressed.

- Host artifact writes check logical names and resolved paths before writing, including direct builtin calls and symlinked output directories.
- All plans restrict file sources to the profiled data root. Explicit TaskSpec sources narrow that allowlist. Profiling skips links outside the root.
- Table metadata records text columns and null positions. Aggregation, joins, reports and Python input/output preserve leading-zero identifiers. Python uses an output schema sidecar, including through Docker.
- Unknown verification flags are rejected and every produced artifact receives schema checks.
- Broken JSON/SQLite files produce per-file profiling issues. Unselected broken files do not block selected valid files.
- README offline examples explicitly select mock. Live examples select Docker and explain which tools still run on the host.
- Original cited evidence is included in docs/evidence/m4-candidate.zip with per-file hashes and a recursive credential-pattern scan.
- The rehearsal script creates outputs before creating its evidence directory.

Verification
- Full suite: 152 passed, one upstream test-client deprecation warning.
- Baseline: 6/6.
- Two real HTTP rehearsals: outputs/m4_rehearsal_l3t36axj.
- Fresh source copy without outputs: both rounds passed at outputs/m4_fresh_tree_mcefo20r/outputs/m4_rehearsal_qh9cz6f3.
- Docker isolation, timeout cleanup, host-crash deadline and cross-source/resume checks passed at outputs/m3_acceptance_kl5ki8zq.
- Docker text ID round-trip passed at outputs/docker_text_ids_hvmkap__.
- No new real-model calls or browser interactions were performed for these corrections. Previous model attempts remain archived separately.
