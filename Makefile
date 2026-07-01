MAIN_SCRIPT    := torque.py
PRIMARY_APP_DIR ?= $(HOME)/.torque/app
TORQUE_RUNTIME_ROOT ?= $(HOME)/.torque/runtime
TORQUE_RUNTIME_VENV ?= $(TORQUE_RUNTIME_ROOT)/venv
TORQUE_RUNTIME_PYTHON ?= $(TORQUE_RUNTIME_VENV)/bin/python
TORQUE_BASE_PYTHON ?= python3
TORQUE_RUNTIME_REQUIREMENTS ?= requirements/desktop.txt
TORQUE_AI_REQUIREMENTS ?= requirements/ai.txt
PRIMARY_PORT    ?= 18933
TORQUE_MIN_PYTHON := 3.10
TORQUE_PYTHON  := $(TORQUE_RUNTIME_PYTHON)

PERF_MATRIX    ?= 10,20,30
PERF_DURATION  ?= 15
PERF_BASELINE  ?= tests/perf/baseline.json
PERF_RUN_DIR   ?= tests/perf/runs
PERF_VENV      ?= $(HOME)/.cache/torque/perf-harness-venv
PERF_PYTHON    ?= $(PERF_VENV)/bin/python
# Test recipes must not inherit Torque runtime/agent env from worker shells.
SANITIZE_TORQUE_TEST_ENV = env $$(env | sed -n 's/^\(TORQUE_[A-Za-z0-9_]*\)=.*/-u \1/p')

.PHONY: install-standalone uninstall run bootstrap deps desktop-deps ai-deps check stop deploy cli standalone standalone-bg desktop desktop-attach tauri-dev tauri-build tauri-build-mac open lint lint-tauri-permissions assert-community-package test test-ee perf-deps perf-baseline perf-delta

## install-standalone: Copy the primary standalone/desktop app files to ~/.torque/app
install-standalone:
	@mkdir -p "$(PRIMARY_APP_DIR)/torque"
	@mkdir -p "$(PRIMARY_APP_DIR)/static/js"
	cp torque.py "$(PRIMARY_APP_DIR)/$(MAIN_SCRIPT)"
	cp torque_desktop.py "$(PRIMARY_APP_DIR)/torque_desktop.py"
	cp webview.html "$(PRIMARY_APP_DIR)/webview.html"
	@find torque -type f \( -name '*.py' -o -name '*.yaml' -o -name '*.yml' \) -print0 | while IFS= read -r -d '' src; do \
		dest="$(PRIMARY_APP_DIR)/$$src"; \
		mkdir -p "$$(dirname "$$dest")"; \
		cp "$$src" "$$dest"; \
	done
	@find static -type f -print0 | while IFS= read -r -d '' src; do \
		dest="$(PRIMARY_APP_DIR)/$$src"; \
		mkdir -p "$$(dirname "$$dest")"; \
		cp "$$src" "$$dest"; \
	done
	@if repo_root=$$(git rev-parse --show-toplevel 2>/dev/null); then \
		printf '%s\n' "$$repo_root" > "$(PRIMARY_APP_DIR)/.torque_source_repo_root"; \
	else \
		rm -f "$(PRIMARY_APP_DIR)/.torque_source_repo_root"; \
	fi
	@echo ""
	@echo "Installed primary standalone/desktop app files to $(PRIMARY_APP_DIR)"
	@echo "Runtime data uses ~/.torque/profiles/<profile>/ (default profile: default)."

## uninstall: Remove primary app files
uninstall:
	rm -rf "$(PRIMARY_APP_DIR)"
	@echo "Uninstalled."

## bootstrap: Create or repair Torque's owned primary runtime venv
bootstrap:
	@base_python="$(TORQUE_BASE_PYTHON)"; \
	if ! command -v "$$base_python" >/dev/null 2>&1; then \
		echo "Error: python3 is required to bootstrap Torque's runtime venv."; \
		echo "Install Python $(TORQUE_MIN_PYTHON)+ and rerun: make deps"; \
		echo "Or set TORQUE_BASE_PYTHON=/path/to/python$(TORQUE_MIN_PYTHON)+"; \
		exit 1; \
	fi; \
	"$$base_python" scripts/bootstrap_runtime_venv.py \
		--python "$$base_python" \
		--venv "$(TORQUE_RUNTIME_VENV)" \
		--requirements "$(TORQUE_RUNTIME_REQUIREMENTS)"

## deps: Install runtime dependencies into Torque's owned primary venv
deps: bootstrap
	@echo "Done. Using Torque runtime: $(TORQUE_RUNTIME_PYTHON)"

## desktop-deps: Compatibility alias; pywebview is included in the primary runtime
desktop-deps: deps
	@echo "pywebview is included in $(TORQUE_RUNTIME_REQUIREMENTS) and installed in: $(TORQUE_RUNTIME_PYTHON)"

## ai-deps: Install optional AI embeddings/indexing dependencies into the runtime venv
ai-deps: deps
	@"$(TORQUE_RUNTIME_PYTHON)" -m pip install -r "$(TORQUE_AI_REQUIREMENTS)"
	@echo "Optional AI dependencies installed in: $(TORQUE_RUNTIME_PYTHON)"

## run: Launch the primary desktop app (native shell backed by standalone daemon)
run: desktop

## stop: Kill any running torque instance (by port)
stop: _check_not_in_worker
	@port="$(or $(TORQUE_PORT),18932)"; \
	pid=$$(lsof -ti TCP:$$port -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pid" ]; then \
		kill $$pid 2>/dev/null; \
		echo "Killed PID $$pid (port $$port)"; \
	else \
		echo "No process on port $$port."; \
	fi

## _check_not_in_worker: Refuse stop/deploy when called from inside a Torque worker
## worktree or with TORQUE_CELL_ID set. The main daemon is the process being killed;
## running this from a worker corrupts the in-memory dispatch pipeline on the
## next boot. Override with FORCE=1 only after reading the failure-mode notes in
## CLAUDE.md → "Never deploy/stop mid-session".
.PHONY: _check_not_in_worker
_check_not_in_worker:
	@if [ -n "$$FORCE" ]; then \
		exit 0; \
	fi; \
	if [ -n "$$TORQUE_CELL_ID" ]; then \
		echo "Error: TORQUE_CELL_ID=$$TORQUE_CELL_ID is set — you are inside a Torque worker."; \
		echo "       \`make stop\`/\`make deploy\` would kill the daemon you are talking to."; \
		echo "       See CLAUDE.md → 'Never deploy/stop mid-session'."; \
		echo "       If you really mean it: FORCE=1 make <target>"; \
		exit 1; \
	fi; \
	case "$$(pwd)" in \
	*.torque/worktrees/*) \
		echo "Error: pwd is under .torque/worktrees/ — you are inside a Torque worker worktree."; \
		echo "       \`make stop\`/\`make deploy\` would kill the daemon that spawned you."; \
		echo "       See CLAUDE.md → 'Never deploy/stop mid-session'."; \
		echo "       If you really mean it: FORCE=1 make <target>"; \
		exit 1; \
		;; \
	esac

## deploy: Stop the primary standalone/desktop daemon and install app files
deploy: _check_not_in_worker deps
	@$(MAKE) --no-print-directory stop TORQUE_PORT="$(or $(TORQUE_PORT),$(PRIMARY_PORT))"
	@$(MAKE) --no-print-directory install-standalone cli
	@echo ""
	@echo "Primary standalone/desktop deploy complete."
	@echo "Relaunch the primary app with: make run"
	@echo "Primary deploy stops port $(or $(TORQUE_PORT),$(PRIMARY_PORT)); set TORQUE_PORT to deploy another runtime port."
	@echo "Browser-only mode remains available with: make standalone (then make open)"

## restart: Deploy the primary app and launch it in one step
restart: deploy run

## cli: Install the torque CLI to ~/.local/bin (add to PATH if needed)
cli:
	@chmod +x bin/torque
	@mkdir -p "$(HOME)/.local/bin"
	@ln -sf "$(CURDIR)/bin/torque" "$(HOME)/.local/bin/torque"
	@echo "Installed: torque → $(CURDIR)/bin/torque"
	@echo "  Symlink: $(HOME)/.local/bin/torque"
	@case "$$PATH" in *$(HOME)/.local/bin*) ;; *) \
		echo ""; \
		echo "  Add to your PATH if not already:"; \
		echo "    export PATH=\"\$$HOME/.local/bin:\$$PATH\"";; \
	esac

## standalone: Run Torque in standalone-only browser mode in the foreground
standalone: deps install-standalone
	@if [ -z "$(TORQUE_RUNTIME_PYTHON)" ]; then \
		echo "Error: Torque runtime Python not found. Run make deps first or set TORQUE_RUNTIME_PYTHON."; \
		exit 1; \
	fi
	@profile="$(or $(TORQUE_PROFILE),default)"; \
	if [ -n "$(TORQUE_DATA_DIR)" ]; then \
		data_dir="$(TORQUE_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.torque/profiles/$$safe_profile"; \
	fi; \
	echo "Starting Torque standalone on http://127.0.0.1:$(or $(TORQUE_PORT),18932)/"; \
	echo "Using standalone data dir: $$data_dir"; \
	echo "Running in the foreground. Keep this shell open; press Ctrl-C to stop."; \
	env TORQUE_STANDALONE=1 TORQUE_PORT="$(or $(TORQUE_PORT),18932)" \
		TORQUE_PROFILE="$$profile" \
		TORQUE_DATA_DIR="$(TORQUE_DATA_DIR)" \
		"$(TORQUE_RUNTIME_PYTHON)" "$(PRIMARY_APP_DIR)/$(MAIN_SCRIPT)"

## standalone-bg: Best-effort detached standalone launch
standalone-bg: deps install-standalone
	@if [ -z "$(TORQUE_RUNTIME_PYTHON)" ]; then \
		echo "Error: Torque runtime Python not found. Run make deps first or set TORQUE_RUNTIME_PYTHON."; \
		exit 1; \
	fi
	@profile="$(or $(TORQUE_PROFILE),default)"; \
	if [ -n "$(TORQUE_DATA_DIR)" ]; then \
		data_dir="$(TORQUE_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.torque/profiles/$$safe_profile"; \
	fi; \
	mkdir -p "$$data_dir"; \
	pid_file="$$data_dir/torque.pid"; \
	nohup env TORQUE_STANDALONE=1 TORQUE_PORT="$(or $(TORQUE_PORT),18932)" \
		TORQUE_PROFILE="$$profile" \
		TORQUE_DATA_DIR="$(TORQUE_DATA_DIR)" \
		"$(TORQUE_RUNTIME_PYTHON)" "$(PRIMARY_APP_DIR)/$(MAIN_SCRIPT)" \
		>> "$$data_dir/torque.log" 2>&1 < /dev/null & \
	pid=$$!; \
	echo "$$pid" > "$$pid_file"; \
	echo "Torque standalone launch requested (PID $$pid). Logs: $$data_dir/torque.log"; \
	echo "Open http://127.0.0.1:$(or $(TORQUE_PORT),18932)/ in a browser"

## desktop: Run Torque in a native pywebview window backed by a standalone server
desktop: deps install-standalone
	@if [ -z "$(TORQUE_RUNTIME_PYTHON)" ]; then \
		echo "Error: Torque runtime Python not found. Run make deps first or set TORQUE_RUNTIME_PYTHON."; \
		exit 1; \
	fi
	@profile="$(or $(TORQUE_PROFILE),default)"; \
	port="$(or $(TORQUE_PORT),18933)"; \
	if [ -n "$(TORQUE_DATA_DIR)" ]; then \
		data_dir="$(TORQUE_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.torque/profiles/$$safe_profile"; \
	fi; \
	echo "Starting Torque desktop shell on http://127.0.0.1:$$port/"; \
	echo "Using runtime profile: $$profile"; \
	echo "Using desktop data dir: $$data_dir"; \
	env TORQUE_DESKTOP_PORT="$$port" \
		TORQUE_DESKTOP_PROFILE="$$profile" \
		TORQUE_DESKTOP_DATA_DIR="$$data_dir" \
		TORQUE_PORT="$$port" \
		TORQUE_PROFILE="$$profile" \
		TORQUE_DATA_DIR="$$data_dir" \
		TORQUE_DESKTOP_MODE="spawn" \
		"$(TORQUE_RUNTIME_PYTHON)" "$(PRIMARY_APP_DIR)/torque_desktop.py"

## desktop-attach: Attach the native shell to an existing matching standalone Torque server
desktop-attach: deps install-standalone
	@if [ -z "$(TORQUE_RUNTIME_PYTHON)" ]; then \
		echo "Error: Torque runtime Python not found. Run make deps first or set TORQUE_RUNTIME_PYTHON."; \
		exit 1; \
	fi
	@profile="$(or $(TORQUE_PROFILE),default)"; \
	port="$(or $(TORQUE_PORT),18933)"; \
	if [ -n "$(TORQUE_DATA_DIR)" ]; then \
		data_dir="$(TORQUE_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.torque/profiles/$$safe_profile"; \
	fi; \
	echo "Attaching Torque desktop shell to http://127.0.0.1:$$port/"; \
	echo "Expecting runtime profile: $$profile"; \
	echo "Expecting standalone data dir: $$data_dir"; \
	env TORQUE_DESKTOP_PORT="$$port" \
		TORQUE_DESKTOP_PROFILE="$$profile" \
		TORQUE_DESKTOP_DATA_DIR="$$data_dir" \
		TORQUE_PORT="$$port" \
		TORQUE_PROFILE="$$profile" \
		TORQUE_DATA_DIR="$$data_dir" \
		TORQUE_DESKTOP_MODE="attach" \
		"$(TORQUE_RUNTIME_PYTHON)" "$(PRIMARY_APP_DIR)/torque_desktop.py"

## tauri-dev: Run Tauri shell in dev mode (live reload, daemon spawned). Equivalent of `make desktop`.
tauri-dev:
	@profile="$(or $(TORQUE_PROFILE),default)"; \
	port="$(or $(TORQUE_PORT),18933)"; \
	if [ -n "$(TORQUE_DATA_DIR)" ]; then \
		data_dir="$(TORQUE_DATA_DIR)"; \
	else \
		safe_profile=$$(printf '%s' "$$profile" \
			| tr '[:upper:]' '[:lower:]' \
			| sed -E 's/[^A-Za-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$$//'); \
		[ -n "$$safe_profile" ] || safe_profile=default; \
		data_dir="$$HOME/.torque/profiles/$$safe_profile"; \
	fi; \
	python="$(or $(TORQUE_PYTHON_EXECUTABLE),$(TORQUE_RUNTIME_PYTHON))"; \
	mode="$${TORQUE_DESKTOP_MODE:-spawn}"; \
	echo "Starting Torque Tauri shell on http://127.0.0.1:$$port/"; \
	echo "Using runtime profile: $$profile"; \
	echo "Using desktop data dir: $$data_dir"; \
	echo "Using Python executable: $$python"; \
	cd src-tauri && env \
		TORQUE_REPO_ROOT="$(CURDIR)" \
		TORQUE_PYTHON_EXECUTABLE="$$python" \
		TORQUE_DESKTOP_PORT="$$port" \
		TORQUE_DESKTOP_PROFILE="$$profile" \
		TORQUE_DESKTOP_DATA_DIR="$$data_dir" \
		TORQUE_PORT="$$port" \
		TORQUE_PROFILE="$$profile" \
		TORQUE_DATA_DIR="$$data_dir" \
		TORQUE_DESKTOP_MODE="$$mode" \
		PATH="$$HOME/.cargo/bin:$$PATH" \
		cargo tauri dev

## tauri-build: Build production Tauri shell for current platform.
tauri-build:
	@cd src-tauri && env TORQUE_REPO_ROOT="$(CURDIR)" PATH="$$HOME/.cargo/bin:$$PATH" cargo tauri build

## tauri-build-mac: Build macOS .app/.dmg (requires macOS host).
tauri-build-mac:
	@cd src-tauri && env TORQUE_REPO_ROOT="$(CURDIR)" PATH="$$HOME/.cargo/bin:$$PATH" cargo tauri build --bundles app,dmg

## open: Open the Torque UI in the default browser (works in dual or standalone mode)
open:
	@open "http://127.0.0.1:$(or $(TORQUE_PORT),18932)/"

## check: Verify prerequisites
check:
	@echo "Torque runtime venv: $(TORQUE_RUNTIME_VENV)"
	@echo "Torque runtime Python: $(TORQUE_RUNTIME_PYTHON)"
	@if [ -x "$(TORQUE_RUNTIME_PYTHON)" ]; then \
		echo "aiohttp:"; \
		"$(TORQUE_RUNTIME_PYTHON)" -c "import aiohttp; print('  installed:', aiohttp.__version__)" 2>/dev/null \
			|| echo "  NOT installed (run: make deps)"; \
		echo "orjson:"; \
		"$(TORQUE_RUNTIME_PYTHON)" -c "import orjson; print('  installed:', orjson.__version__)" 2>/dev/null \
			|| echo "  NOT installed (run: make deps)"; \
		echo "pywebview:"; \
		"$(TORQUE_RUNTIME_PYTHON)" -c "import webview; print('  installed:', getattr(webview, '__version__', 'unknown'))" 2>/dev/null \
			|| echo "  NOT installed (run: make deps)"; \
	else \
		echo "  Runtime venv not ready (run: make deps)"; \
	fi
	@echo "Primary app:  $(PRIMARY_APP_DIR)"
	@test -f "$(PRIMARY_APP_DIR)/$(MAIN_SCRIPT)" \
		&& echo "Primary app installed: yes" \
		|| echo "Primary app installed: no (run: make deploy)"

## lint: Run repository lint checks
lint: lint-tauri-permissions assert-community-package

## lint-tauri-permissions: Ensure every registered Tauri command has a local permission
lint-tauri-permissions:
	@python3 scripts/lint_tauri_permissions.py

## assert-community-package: Ensure community install artifacts exclude ee/
assert-community-package:
	@python3 scripts/assert_community_package_excludes_ee.py

## test: Run the automated regression suite
test: lint
	@$(SANITIZE_TORQUE_TEST_ENV) python3 -m unittest discover -s tests -v

## test-ee: Run enterprise-only regression tests (requires ee/ checkout)
test-ee: lint
	@for path in \
		ee \
		ee/python/torque_ee_connector \
		ee/frontend/remote/js \
		ee/LICENSE \
		ee/frontend/README.md \
		ee/python/README.md \
		ee/relay/README.md; do \
		[ -e "$$path" ] || { echo "Error: $$path is required for make test-ee"; exit 1; }; \
	done
	@$(SANITIZE_TORQUE_TEST_ENV) TORQUE_WITH_EE=1 python3 -m unittest -v \
		tests.test_ee_connector \
		tests.test_ee_license_boundary \
		tests.test_ee_python_package \
		tests.test_relay_probe \
		tests.test_frontend_remote

## perf-deps: Prepare the cached Python environment used by perf harness targets
perf-deps:
	@if [ ! -x "$(PERF_PYTHON)" ]; then \
		mkdir -p "$$(dirname "$(PERF_VENV)")"; \
		python3 -m venv "$(PERF_VENV)"; \
	fi
	@"$(PERF_PYTHON)" -c "import aiohttp, jinja2, yaml" >/dev/null 2>&1 || \
		"$(PERF_PYTHON)" -m pip install -q aiohttp jinja2 pyyaml

## perf-baseline: Capture N=10/20/30 standalone perf baseline evidence
perf-baseline: perf-deps
	@mkdir -p "$$(dirname "$(PERF_BASELINE)")"
	@"$(PERF_PYTHON)" scripts/profile_harness.py \
		--mode baseline \
		--matrix "$(PERF_MATRIX)" \
		--duration "$(PERF_DURATION)" \
		--output "$(PERF_BASELINE)" \
		--report "$(PERF_BASELINE:.json=.md)"

## perf-delta: Capture current perf evidence and diff against PERF_BASELINE
perf-delta: perf-deps
	@mkdir -p "$(PERF_RUN_DIR)"
	@stamp=$$(date -u +%Y%m%dT%H%M%SZ); \
	"$(PERF_PYTHON)" scripts/profile_harness.py \
		--mode delta \
		--matrix "$(PERF_MATRIX)" \
		--duration "$(PERF_DURATION)" \
		--baseline "$(PERF_BASELINE)" \
		--output "$(PERF_RUN_DIR)/delta-$$stamp.json" \
		--report "$(PERF_RUN_DIR)/delta-$$stamp.md"
