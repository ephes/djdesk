Historical migrations and rollback
==================================

Before an upgrade or schema rollback, stop DJDesk and all workers writing to the
database and take a restorable database backup. Test a restore on a disposable
copy first. Always select the same settings and database alias for inspection,
repair and migration, for example ``python manage.py migrate --database=secondary``.

Inspector migrations 0002, 0003 and 0004 bind their historical queries and writes
to that selected alias. Migrations 0002 and 0003 contain frozen tutorial values;
changes to runtime ``content.py`` or asset helpers do not rewrite installation
history. The initial sample workspace location still honors
``INSPECTOR_SAMPLE_ROOT`` (configured through ``DJDESK_SAMPLE_ROOT``), defaulting
to ``BASE_DIR / "sample_projects"``. Existing applied migrations are not replayed:
these safeguards affect future installations, historical restores and rollbacks.
Do not fake-unapply/reapply them to update an existing installation's content.

Seed rollback
-------------

Migration 0002 cannot be reversed. Seed rows may have been customized and have
user-created scans or task history; their current values cannot establish safe
ownership for deletion. Django raises ``IrreversibleError`` before running any
reverse operation in 0002. To return to a database predating the seed, restore
the matching backup together with the matching application code. A rollback
plan from a later migration can undo earlier steps in that plan before reaching
this refusal; inspect the plan and backup first.

Workspace path repair
---------------------

Migration 0004 supports SQLite and PostgreSQL. It checks all workspaces before
changing any rows. An empty path after stripping surrounding whitespace is
refused with at most ten workspace IDs. A normalized duplicate group is refused
if its name, Python/Django version, description, docs URL, metadata,
``manage_py_detected`` or ``last_scan_at`` differs. Diagnostics give conflicting
row IDs and field names, without including paths or metadata values.

On refusal, keep writers stopped and inspect those IDs using the historical
schema at inspector 0003 on the selected alias. Give distinct workspaces their
correct distinct paths. Reconcile conflicting data explicitly after retaining
the originals in the backup; no automatic path invention or conflict deletion
is performed. Retry migration 0004 after repair.

Equivalent duplicates retain the row whose original path sorts first in binary
order, breaking ties by primary key. That canonical ID, slug and creation/update
timestamps survive; the other identity rows and their slugs/timestamps retire.
All scans and task runs move to the canonical workspace without changing their
other fields. Surrounding whitespace uses the frozen Python whitespace set,
including tabs, newlines and Unicode spaces; path case and interior characters
remain significant.

Preflight and application use ordered projections in batches of at most 200,
retaining only the current group's canonical row across batches. Child moves,
duplicate deletion and path updates are batched in the selected database's
transaction. A failed migration rolls back its changes. Other database engines
are refused before mutation rather than given different trimming semantics.

Reversing 0004 to 0003 removes its index/unique constraint and leaves the
normalized workspace data and child relationships intact. It does not recreate
retired duplicate identities or untrimmed paths; restore the backup if those
are needed. Reversing 0003 removes the ``pane_target`` field, so preserve any
customized values in the backup before doing so.

Run ``DJANGO_SETTINGS_MODULE=djdesk.settings.test uv run python manage.py test
tests.test_content_migrations`` for disposable two-alias migration regressions.
