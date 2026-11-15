Sample publication and recovery
===============================

Run ``just fetch-samples`` to create missing tutorial samples. To replace an entire
sample, including local edits and databases, run
``uv run python scripts/fetch_samples.py --force``. Use ``--root PATH`` for an isolated
sample root. Stop sample applications before replacing their files.

Publishers wait at most ten seconds for each stable ``.slug.lock`` and hold it through
preflight, swap, rollback and cleanup. Never delete the lock while publishers may be
running. Native locks are required; unsupported locking refuses publication rather
than falling back to a marker that survives process death. A non-force caller rechecks the destination after taking ownership.

The replacement is written in ``.slug-<random>/replacement`` beside the destination.
All staged files and directories are synced before an owner-only ``.slug.swap.json``
intent records the root, staging, and old/new directory identities. The intent and
rename parents are synced before the old backup is retired. Newly created sample-root
ancestors are synced too. macOS also flushes the
drive cache with ``F_FULLFSYNC`` where supported. Other filesystems use fsync.
Directory fsync and no-follow directory opens are required: unsupported filesystems
and Windows refuse creation/replacement before changing an existing sample. Skipping
an existing sample without force still works.

A failed install attempts to restore the old sample. If restoration fails, the error
names the retained recovery files. After a successful rename, a sync failure reports
that the new sample is already installed but its durability is uncertain. It leaves
the backup and staging in place and does not roll back or retry automatically. A
later cleanup error also reports the installed sample; only after the new generation
is durable may a leftover backup be partially removed.

Restart after interruption
--------------------------

Run the same fetch command **without force** first. Under the same publisher lock,
the intent is reconciled before the usual existence check:

* If the public name is missing, restore the identity-matched old backup. For a first
  install without an old sample, complete the staged generation instead.
* If the new generation is already visible, accept it and retain its old backup.
* If the old name was never removed or rollback finished, keep it.
* If any directory identity or intent is unexpected, stop for manual inspection and
  preserve all related paths.

Recovery syncs the rename parents before retiring the intent. A recovered sample is
kept for this invocation even with ``--force``; inspect it before a separate force
replacement, so a restart cannot immediately delete the recovered edits. It never automatically
deletes interrupted staging trees or backups, including user edits made after a crash.
The warning names the retained directory. With all publishers stopped, copy valuable
files elsewhere and inspect the public sample, ``backup``, ``replacement`` and journal
before manual repair. Remove a journal only after establishing a complete public
generation and accounting for all retained files. An interruption before intent
creation can leave an unreferenced staging directory; inspect that manually as well.

The two renames form a recoverable swap, not an atomic directory exchange. Readers
can briefly observe a missing public name. Tests use synthetic files, real publisher
subprocesses killed at each rename boundary, and injected file/directory sync failures:
``uv run python manage.py test tests.test_samples tests.test_sample_processes
tests.test_sample_durability tests.test_sample_intent --settings=djdesk.settings.test``.
