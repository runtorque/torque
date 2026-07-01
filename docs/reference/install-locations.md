# Install locations

Primary standalone/desktop app files are installed to:

```text
~/.torque/app/
```

The primary Python runtime is Torque-owned and lives outside the app copy and
outside the legacy iTerm2 Application Support tree:

```text
~/.torque/runtime/venv/bin/python
```

`make deps` (and `make deploy` before it copies app files) creates or repairs
that clean virtual environment with `python3 -m venv` and installs the primary
runtime requirements. `make deploy` is non-destructive with respect to old
AppSupport installs: it does **not** delete, move, or rewrite legacy Toolbelt
runtime/data files. Existing legacy data must be migrated deliberately by the
operator.

Primary runtime data is profile-scoped:

```text
~/.torque/profiles/default/torque.db       # make run and make standalone defaults
~/.torque/profiles/default/torque.log
```

CLI offline reads and `torque logs` default to the shared default profile. Use
`TORQUE_PROFILE` or `TORQUE_DATA_DIR` to target another profile/data directory. If no primary
profile DB/log exists and no profile/data-dir was requested, the CLI may fall
back to an existing legacy Toolbelt DB/log so old installs remain inspectable
long enough to migrate.

Deprecated secondary Toolbelt files from older releases may still exist at:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/
```

Toolbelt runtime data created by those older daemons remains in the Toolbelt
script directory until you manually migrate or remove it:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db    # SQLite state
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log   # daemon log
```

The AppSupport Python environment is legacy and Toolbelt-only. The primary
surfaces are the desktop app (`make run`) and standalone browser mode
(`make standalone`). The Makefile no longer installs, updates, or launches the
old Toolbelt Scripts copy. Migrate Toolbelt data to a profile with
`scripts/migrate_toolbelt_to_profile.py` (TORQUE:645 P1b) before deleting any
legacy DB/log files:

```text
~/Library/Application Support/iTerm2/Scripts/torque/iterm2env/versions/3.14.0/bin/python3
~/.config/iterm2/AppSupport/iterm2env-*/versions/*/bin/python3
```

`torque doctor` reports a `[runtime_locations]` section and warns when it is
reading the legacy Toolbelt data directory or when a live daemon is still using
a legacy AppSupport Python runtime. These warnings are diagnostic only; cleanup
remains manual and non-destructive.
