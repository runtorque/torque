ITERM2_SCRIPTS := $(HOME)/Library/Application Support/iTerm2/Scripts
ITERM2_PROJECT := $(ITERM2_SCRIPTS)/loom
SCRIPT_DIR     := $(ITERM2_PROJECT)/loom
MAIN_SCRIPT    := loom.py
AUTOLAUNCH_DIR := $(ITERM2_SCRIPTS)/AutoLaunch

# Global iTerm2 Python environment (used to bootstrap the project env)
GLOBAL_ENV     := $(shell ls -d $(HOME)/.config/iterm2/AppSupport/iterm2env-[0-9]* 2>/dev/null \
                    | sort -V | tail -1)

# Project-local Python (preferred once the env exists)
PROJECT_PYTHON := $(shell ls "$(ITERM2_PROJECT)"/iterm2env/versions/3.*/bin/python3 \
                    2>/dev/null | sort -V | tail -1)
GLOBAL_PYTHON  := $(shell ls $(HOME)/.config/iterm2/AppSupport/iterm2env*/versions/*/bin/python3 \
                    2>/dev/null | sort -V | tail -1)
ITERM2_PYTHON  := $(or $(PROJECT_PYTHON),$(GLOBAL_PYTHON))

PERF_MATRIX    ?= 10,20,30
PERF_DURATION  ?= 15
PERF_BASELINE  ?= tests/perf/baseline.json
PERF_RUN_DIR   ?= tests/perf/runs

.PHONY: install uninstall run deps desktop-deps check stop deploy autolaunch cli standalone standalone-bg desktop desktop-attach open test perf-baseline perf-delta

## install: Set up the iTerm2 script project and copy all files
install:
	@# -- Ensure project directory exists --
	@mkdir -p "$(SCRIPT_DIR)/loom"
	@mkdir -p "$(SCRIPT_DIR)/loom/adapters"
	@mkdir -p "$(SCRIPT_DIR)/static/js"
	@# -- Bootstrap iterm2env if missing --
	@if [ ! -d "$(ITERM2_PROJECT)/iterm2env" ]; then \
		if [ -n "$(GLOBAL_ENV)" ] && [ -d "$(GLOBAL_ENV)" ]; then \
			echo "Linking iterm2env from $(GLOBAL_ENV)"; \
			ln -sf "$(GLOBAL_ENV)" "$(ITERM2_PROJECT)/iterm2env"; \
		else \
			echo "Error: No iTerm2 Python environment found."; \
			echo "Open iTerm2 Preferences → General → Magic → Enable Python API,"; \
			echo "then create any script via Scripts → Manage → New Python Script"; \
			echo "to trigger the initial environment download."; \
			exit 1; \
		fi; \
	fi
	@# -- Generate setup.cfg if missing --
	@if [ ! -f "$(ITERM2_PROJECT)/setup.cfg" ]; then \
		PY_VER=$$(ls "$(ITERM2_PROJECT)/iterm2env/versions/" 2>/dev/null \
		          | sort -V | tail -1); \
		echo "Generating setup.cfg (python=$${PY_VER:-3.14})"; \
		printf '%s\n' \
			'[metadata]' \
			'name=loom' \
			'version=2.0.0' \
			'' \
			'[options]' \
			'scripts=loom/loom.py' \
			'install_requires=iterm2' \
			"python_requires = =$${PY_VER:-3.14}" \
			'' \
			'[iterm2]' \
			'environment = >=79' \
			> "$(ITERM2_PROJECT)/setup.cfg"; \
	fi
	@# -- Copy source files --
	cp loom.py "$(SCRIPT_DIR)/$(MAIN_SCRIPT)"
	cp loom_desktop.py "$(SCRIPT_DIR)/loom_desktop.py"
	cp webview.html "$(SCRIPT_DIR)/webview.html"
	@find loom -type f -name '*.py' -print0 | while IFS= read -r -d '' src; do \
		dest="$(SCRIPT_DIR)/$$src"; \
		mkdir -p "$$(dirname "$$dest")"; \
		cp "$$src" "$$dest"; \
	done
	@find static -type f -print0 | while IFS= read -r -d '' src; do \
		dest="$(SCRIPT_DIR)/$$src"; \
		mkdir -p "$$(dirname "$$dest")"; \
		cp "$$src" "$$dest"; \
	done
	@if repo_root=$$(git rev-parse --show-toplevel 2>/dev/null); then \
		printf '%s\n' "$$repo_root" > "$(SCRIPT_DIR)/.loom_source_repo_root"; \
	else \
		rm -f "$(SCRIPT_DIR)/.loom_source_repo_root"; \
	fi
	@# -- Install dependencies --
	@PYTHON="$(or $(PROJECT_PYTHON),$(shell ls "$(ITERM2_PROJECT)"/iterm2env/versions/3.*/bin/python3 \
	    2>/dev/null | sort -V | tail -1))"; \
	if [ -n "$$PYTHON" ]; then \
		"$$PYTHON" -m pip install -q aiohttp jinja2 pyyaml 2>/dev/null || true; \
	fi
	@echo ""
	@echo "Installed to $(SCRIPT_DIR)"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Run: Scripts menu → loom"
	@echo "  2. Show Toolbelt: View → Show Toolbelt (⌘⇧B)"
	@echo "  3. Check 'Loom' in the Toolbelt gear menu"

## autolaunch: Symlink for auto-start on iTerm2 launch
autolaunch: install
	@mkdir -p "$(AUTOLAUNCH_DIR)"
	ln -sf "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" "$(AUTOLAUNCH_DIR)/$(MAIN_SCRIPT)"
	@echo "Auto-launch symlink created."

## uninstall: Remove installed files and autolaunch symlink
uninstall:
	rm -f "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" "$(SCRIPT_DIR)/loom_desktop.py" "$(SCRIPT_DIR)/webview.html" "$(SCRIPT_DIR)/state.json" "$(SCRIPT_DIR)/loom.db" "$(SCRIPT_DIR)/.loom_source_repo_root"
	rm -f "$(AUTOLAUNCH_DIR)/$(MAIN_SCRIPT)"
	@echo "Uninstalled."

## deps: Install aiohttp into iTerm2's Python environment
deps:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	"$(ITERM2_PYTHON)" -m pip install aiohttp jinja2 pyyaml
	@echo "Done. Using: $(ITERM2_PYTHON)"

## desktop-deps: Install optional native desktop-shell dependency
desktop-deps:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	"$(ITERM2_PYTHON)" -m pip install pywebview
	@echo "Installed pywebview into: $(ITERM2_PYTHON)"

## run: Launch the script in the background (iTerm2 must be running with Python API enabled)
run:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	@pid_file="$(SCRIPT_DIR)/loom.pid"; \
	profile="$(LOOM_PROFILE)"; \
	if [ -n "$(LOOM_DATA_DIR)" ]; then \
		data_dir="$(LOOM_DATA_DIR)"; \
	elif [ -n "$$profile" ]; then \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.loom/profiles/$$safe_profile"; \
	else \
		data_dir="$(SCRIPT_DIR)"; \
	fi; \
	mkdir -p "$$data_dir"; \
	nohup env LOOM_PORT="$(or $(LOOM_PORT),18932)" \
		LOOM_PROFILE="$$profile" \
		LOOM_DATA_DIR="$(LOOM_DATA_DIR)" \
		"$(ITERM2_PYTHON)" "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" \
		>> "$$data_dir/loom.log" 2>&1 < /dev/null & \
	pid=$$!; \
	echo "$$pid" > "$$pid_file"; \
	echo "Loom started (PID $$pid). Logs: $$data_dir/loom.log"

## stop: Kill any running loom instance (by port)
stop:
	@port="$(or $(LOOM_PORT),18932)"; \
	pid=$$(lsof -ti TCP:$$port -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pid" ]; then \
		kill $$pid 2>/dev/null; \
		echo "Killed PID $$pid (port $$port)"; \
	else \
		echo "No process on port $$port."; \
	fi

## deploy: Stop old instance, install new files, prompt to restart
deploy: stop install
	@echo ""
	@echo "Now restart via: make run (or Scripts menu → loom)"

## restart: Deploy and launch in one step
restart: deploy run

## cli: Install the loom CLI to ~/.local/bin (add to PATH if needed)
cli:
	@chmod +x bin/loom
	@mkdir -p "$(HOME)/.local/bin"
	@ln -sf "$(CURDIR)/bin/loom" "$(HOME)/.local/bin/loom"
	@echo "Installed: loom → $(CURDIR)/bin/loom"
	@echo "  Symlink: $(HOME)/.local/bin/loom"
	@case "$$PATH" in *$(HOME)/.local/bin*) ;; *) \
		echo ""; \
		echo "  Add to your PATH if not already:"; \
		echo "    export PATH=\"\$$HOME/.local/bin:\$$PATH\"";; \
	esac

## standalone: Run Loom in standalone-only mode in the foreground
standalone: install
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	@profile="$(or $(LOOM_PROFILE),standalone)"; \
	if [ -n "$(LOOM_DATA_DIR)" ]; then \
		data_dir="$(LOOM_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.loom/profiles/$$safe_profile"; \
	fi; \
	echo "Starting Loom standalone on http://127.0.0.1:$(or $(LOOM_PORT),18932)/"; \
	echo "Using standalone data dir: $$data_dir"; \
	echo "Running in the foreground. Keep this shell open; press Ctrl-C to stop."; \
	env LOOM_STANDALONE=1 LOOM_PORT="$(or $(LOOM_PORT),18932)" \
		LOOM_PROFILE="$$profile" \
		LOOM_DATA_DIR="$(LOOM_DATA_DIR)" \
		"$(ITERM2_PYTHON)" "$(SCRIPT_DIR)/$(MAIN_SCRIPT)"

## standalone-bg: Best-effort detached standalone launch
standalone-bg: install
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	@pid_file="$(SCRIPT_DIR)/standalone.pid"; \
	profile="$(or $(LOOM_PROFILE),standalone)"; \
	if [ -n "$(LOOM_DATA_DIR)" ]; then \
		data_dir="$(LOOM_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.loom/profiles/$$safe_profile"; \
	fi; \
	mkdir -p "$$data_dir"; \
	nohup env LOOM_STANDALONE=1 LOOM_PORT="$(or $(LOOM_PORT),18932)" \
		LOOM_PROFILE="$$profile" \
		LOOM_DATA_DIR="$(LOOM_DATA_DIR)" \
		"$(ITERM2_PYTHON)" "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" \
		>> "$$data_dir/loom.log" 2>&1 < /dev/null & \
	pid=$$!; \
	echo "$$pid" > "$$pid_file"; \
	echo "Loom standalone launch requested (PID $$pid). Logs: $$data_dir/loom.log"; \
	echo "Open http://127.0.0.1:$(or $(LOOM_PORT),18932)/ in a browser"

## desktop: Run Loom in a native pywebview window backed by a standalone server
desktop:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	@profile="$(or $(LOOM_PROFILE),desktop)"; \
	port="$(or $(LOOM_PORT),18933)"; \
	if [ -n "$(LOOM_DATA_DIR)" ]; then \
		data_dir="$(LOOM_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.loom/profiles/$$safe_profile"; \
	fi; \
	echo "Starting Loom desktop shell on http://127.0.0.1:$$port/"; \
	echo "Using desktop profile: $$profile"; \
	echo "Using desktop data dir: $$data_dir"; \
	env LOOM_DESKTOP_PORT="$$port" \
		LOOM_DESKTOP_PROFILE="$$profile" \
		LOOM_DESKTOP_DATA_DIR="$$data_dir" \
		LOOM_PORT="$$port" \
		LOOM_PROFILE="$$profile" \
		LOOM_DATA_DIR="$$data_dir" \
		LOOM_DESKTOP_MODE="spawn" \
		"$(ITERM2_PYTHON)" "$(CURDIR)/loom_desktop.py"

## desktop-attach: Attach the native shell to an existing matching standalone Loom server
desktop-attach:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	@profile="$(or $(LOOM_PROFILE),desktop)"; \
	port="$(or $(LOOM_PORT),18933)"; \
	if [ -n "$(LOOM_DATA_DIR)" ]; then \
		data_dir="$(LOOM_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.loom/profiles/$$safe_profile"; \
	fi; \
	echo "Attaching Loom desktop shell to http://127.0.0.1:$$port/"; \
	echo "Expecting standalone profile: $$profile"; \
	echo "Expecting standalone data dir: $$data_dir"; \
	env LOOM_DESKTOP_PORT="$$port" \
		LOOM_DESKTOP_PROFILE="$$profile" \
		LOOM_DESKTOP_DATA_DIR="$$data_dir" \
		LOOM_PORT="$$port" \
		LOOM_PROFILE="$$profile" \
		LOOM_DATA_DIR="$$data_dir" \
		LOOM_DESKTOP_MODE="attach" \
		"$(ITERM2_PYTHON)" "$(CURDIR)/loom_desktop.py"

## open: Open the Loom UI in the default browser (works in dual or standalone mode)
open:
	@open "http://127.0.0.1:$(or $(LOOM_PORT),18932)/"

## check: Verify prerequisites
check:
	@echo "iTerm2 Python: $(or $(ITERM2_PYTHON),NOT FOUND)"
	@if [ -n "$(ITERM2_PYTHON)" ]; then \
		echo "aiohttp:"; \
		"$(ITERM2_PYTHON)" -c "import aiohttp; print('  installed:', aiohttp.__version__)" 2>/dev/null \
			|| echo "  NOT installed (run: make deps)"; \
		echo "pywebview:"; \
		"$(ITERM2_PYTHON)" -c "import webview; print('  installed:', getattr(webview, '__version__', 'unknown'))" 2>/dev/null \
			|| echo "  NOT installed (run: make desktop-deps)"; \
		echo "iterm2:"; \
		"$(ITERM2_PYTHON)" -c "import iterm2; print('  installed')" 2>/dev/null \
			|| echo "  NOT installed"; \
	fi
	@echo "Project dir:  $(ITERM2_PROJECT)"
	@echo "iterm2env:    $(shell test -e "$(ITERM2_PROJECT)/iterm2env" && echo 'yes' || echo 'no')"
	@echo "setup.cfg:    $(shell test -f "$(ITERM2_PROJECT)/setup.cfg" && echo 'yes' || echo 'no')"
	@test -f "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" \
		&& echo "Installed:    yes" \
		|| echo "Installed:    no (run: make install)"

## test: Run the automated regression suite
test:
	@python3 -m unittest discover -s tests -v

## perf-baseline: Capture N=10/20/30 standalone perf baseline evidence
perf-baseline:
	@mkdir -p "$$(dirname "$(PERF_BASELINE)")"
	@python3 scripts/profile_harness.py \
		--mode baseline \
		--matrix "$(PERF_MATRIX)" \
		--duration "$(PERF_DURATION)" \
		--output "$(PERF_BASELINE)" \
		--report "$(PERF_BASELINE:.json=.md)"

## perf-delta: Capture current perf evidence and diff against PERF_BASELINE
perf-delta:
	@mkdir -p "$(PERF_RUN_DIR)"
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); \
	python3 scripts/profile_harness.py \
		--mode delta \
		--matrix "$(PERF_MATRIX)" \
		--duration "$(PERF_DURATION)" \
		--baseline "$(PERF_BASELINE)" \
		--output "$(PERF_RUN_DIR)/delta-$$stamp.json" \
		--report "$(PERF_RUN_DIR)/delta-$$stamp.md"
