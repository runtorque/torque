# Install locations

Primary standalone/desktop app files are installed to:
```
~/.torque/app/
```
Primary runtime data is profile-scoped:
```
~/.torque/profiles/desktop/torque.db       # make run / desktop profile
~/.torque/profiles/desktop/torque.log
~/.torque/profiles/standalone/torque.db    # make standalone
~/.torque/profiles/standalone/torque.log
```

Secondary iTerm2 Toolbelt files are installed by `make deploy-toolbelt` to:
```
~/Library/Application Support/iTerm2/Scripts/torque/torque/
```
Toolbelt runtime data (created by the daemon, not installed by `make deploy-toolbelt`):
```
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db    # SQLite state
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log   # daemon log
```
This is an iTerm2 "full environment" script project with its own bundled Python 3.14 at:
```
~/Library/Application Support/iTerm2/Scripts/torque/iterm2env/versions/3.14.0/bin/python3
```
