ITERM2_PROJECT := $(HOME)/Library/Application Support/iTerm2/Scripts/iterm2-agent-orchestrator
SCRIPT_DIR     := $(ITERM2_PROJECT)/iterm2-agent-orchestrator
AUTOLAUNCH_DIR := $(HOME)/Library/Application Support/iTerm2/Scripts/AutoLaunch
MAIN_SCRIPT    := iterm2-agent-orchestrator.py

# Prefer the project-local Python (matches setup.cfg python_requires),
# fall back to the global iTerm2 Python.
PROJECT_PYTHON := $(shell ls "$(ITERM2_PROJECT)"/iterm2env/versions/3.*/bin/python3 2>/dev/null | sort -V | tail -1)
GLOBAL_PYTHON  := $(shell ls $(HOME)/.config/iterm2/AppSupport/iterm2env*/versions/*/bin/python3 2>/dev/null | head -1)
ITERM2_PYTHON  := $(or $(PROJECT_PYTHON),$(GLOBAL_PYTHON))

.PHONY: install uninstall run deps check stop deploy

## install: Copy agent_matrix files into the iTerm2 Scripts project
install:
	@mkdir -p "$(SCRIPT_DIR)/agent_matrix"
	cp agent_matrix.py "$(SCRIPT_DIR)/$(MAIN_SCRIPT)"
	cp webview.html    "$(SCRIPT_DIR)/webview.html"
	cp agent_matrix/__init__.py agent_matrix/config.py \
	   agent_matrix/state.py agent_matrix/bridge.py \
	   agent_matrix/server.py "$(SCRIPT_DIR)/agent_matrix/"
	@echo ""
	@echo "Installed to $(SCRIPT_DIR)"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Run: Scripts menu → iterm2-agent-orchestrator"
	@echo "  2. Show Toolbelt: View → Show Toolbelt (⌘⇧B)"
	@echo "  3. Check 'Agent Matrix' in the Toolbelt gear menu"

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
		echo "Error: iTerm2 Python not found at ~/.config/iterm2/AppSupport/iterm2env*/"; \
		exit 1; \
	fi
	"$(ITERM2_PYTHON)" -m pip install aiohttp
	@echo "Done. Using: $(ITERM2_PYTHON)"

## run: Launch the script directly (iTerm2 must be running with Python API enabled)
run:
	@if [ -z "$(ITERM2_PYTHON)" ]; then \
		echo "Error: iTerm2 Python not found at ~/.config/iterm2/AppSupport/iterm2env*/"; \
		exit 1; \
	fi
	"$(ITERM2_PYTHON)" "$(SCRIPT_DIR)/$(MAIN_SCRIPT)"

## stop: Kill any running agent_matrix instance (by port)
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
	@echo "Now restart via: Scripts menu → iterm2-agent-orchestrator"

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
	@echo "Script dir:   $(SCRIPT_DIR)"
	@test -f "$(SCRIPT_DIR)/$(MAIN_SCRIPT)" \
		&& echo "Installed:    yes" \
		|| echo "Installed:    no (run: make install)"
