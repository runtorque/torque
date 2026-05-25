# Install locations

Primary standalone/desktop app files are installed to:

```text
~/.torque/app/
```

The primary Python runtime is Torque-owned and lives outside the app copy and
outside iTerm2's Application Support tree:

```text
~/.torque/runtime/venv/bin/python
```

`make deps` (and `make deploy` before it copies app files) creates or repairs
that clean virtual environment with `python3 -m venv` and installs the primary
runtime requirements. Existing AppSupport/iTerm2 installs are not reused or
moved; on the next deploy, Torque rebuilds the primary venv in
`~/.torque/runtime/venv`.

Primary runtime data is profile-scoped:

```text
~/.torque/profiles/desktop/torque.db       # make run / desktop profile
~/.torque/profiles/desktop/torque.log
~/.torque/profiles/standalone/torque.db    # make standalone
~/.torque/profiles/standalone/torque.log
```

Deprecated secondary iTerm2 Toolbelt files from older releases may still exist
at:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/
```

Toolbelt runtime data created by those older daemons remains in the Toolbelt
script directory:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db    # SQLite state
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log   # daemon log
```

The iTerm2/AppSupport Python environment is legacy and Toolbelt-only. The
primary surfaces are the desktop app (`make run`) and standalone browser mode
(`make standalone`). The Makefile no longer installs, updates, or launches the
old Toolbelt Scripts copy. Migrate Toolbelt data to a profile with
`scripts/migrate_toolbelt_to_profile.py` (TORQUE:645 P1b):

```text
~/Library/Application Support/iTerm2/Scripts/torque/iterm2env/versions/3.14.0/bin/python3
~/.config/iterm2/AppSupport/iterm2env-*/versions/*/bin/python3
```
