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

.PHONY: install uninstall run deps check stop deploy autolaunch

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
			'version=1.0' \
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
	cp webview.html "$(SCRIPT_DIR)/webview.html"
	cp loom/__init__.py loom/config.py \
	   loom/state.py loom/bridge.py \
	   loom/server.py loom/keybindings.py \
	   loom/events.py loom/notifications.py \
	   "$(SCRIPT_DIR)/loom/"
	cp loom/adapters/__init__.py loom/adapters/base.py \
	   loom/adapters/claude_code.py loom/adapters/codex.py \
	   loom/adapters/gemini_cli.py loom/adapters/generic.py \
	   "$(SCRIPT_DIR)/loom/adapters/"
	cp static/style.css "$(SCRIPT_DIR)/static/"
	cp static/js/*.js   "$(SCRIPT_DIR)/static/js/"
	@# -- Install dependencies --
	@PYTHON="$(or $(PROJECT_PYTHON),$(shell ls "$(ITERM2_PROJECT)"/iterm2env/versions/3.*/bin/python3 \
	    2>/dev/null | sort -V | tail -1))"; \
	if [ -n "$$PYTHON" ]; then \
		"$$PYTHON" -m pip install -q aiohttp 2>/dev/null || true; \
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
	rm -f "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" "$(SCRIPT_DIR)/webview.html" "$(SCRIPT_DIR)/state.json"
	rm -f "$(AUTOLAUNCH_DIR)/$(MAIN_SCRIPT)"
	@echo "Uninstalled."

## deps: Install aiohttp into iTerm2's Python environment
deps:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	"$(ITERM2_PYTHON)" -m pip install aiohttp
	@echo "Done. Using: $(ITERM2_PYTHON)"

## run: Launch the script directly (iTerm2 must be running with Python API enabled)
run:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found. Run make install first."; \
		exit 1; \
	fi
	"$(ITERM2_PYTHON)" "$(SCRIPT_DIR)/$(MAIN_SCRIPT)"

## stop: Kill any running loom instance (by port)
stop:
	@pid=$$(lsof -ti :18932 2>/dev/null); \
	if [ -n "$$pid" ]; then \
		kill $$pid 2>/dev/null; \
		echo "Killed PID $$pid (port 18932)"; \
	else \
		echo "No process on port 18932."; \
	fi

## deploy: Stop old instance, install new files, prompt to restart
deploy: stop install
	@echo ""
	@echo "Now restart via: Scripts menu → loom"

## check: Verify prerequisites
check:
	@echo "iTerm2 Python: $(or $(ITERM2_PYTHON),NOT FOUND)"
	@if [ -n "$(ITERM2_PYTHON)" ]; then \
		echo "aiohttp:"; \
		"$(ITERM2_PYTHON)" -c "import aiohttp; print('  installed:', aiohttp.__version__)" 2>/dev/null \
			|| echo "  NOT installed (run: make deps)"; \
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
