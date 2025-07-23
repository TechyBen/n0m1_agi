#!/usr/bin/env python3
"""
Enhanced boot system for n0m1_agi with signal handling and health monitoring.
"""
import subprocess
import os
import sys
import time
import signal
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional
from manager_utils import get_venv_python

# --- Configuration ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON_PATH = get_venv_python(PROJECT_DIR)
CONFIG_FILE = os.path.join(PROJECT_DIR, 'config.json')
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')

# Manager configuration with health check intervals
MANAGER_CONFIG = {
    'daemon_manager.py': {
        'name': 'Daemon Manager',
        'startup_delay': 1,
        'health_check_interval': 30,
        'critical': True
    },
    'nano_manager.py': {
        'name': 'Nano Manager', 
        'startup_delay': 1,
        'health_check_interval': 30,
        'critical': True
    },
    'main_llm_manager.py': {
        'name': 'Main LLM Manager',
        'startup_delay': 2,
        'health_check_interval': 60,
        'critical': False
    }
}

LOGS_DIR = os.path.join(PROJECT_DIR, "logs_managers")
PID_FILE = os.path.join(PROJECT_DIR, "pids", "boot_system.pid")
# --- End Configuration ---

class BootSystem:
    def __init__(self):
        self.launched_managers: Dict[str, subprocess.Popen] = {}
        self.shutdown_requested = False
        self.start_time = datetime.now()
        
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGHUP, self._reload_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        sig_name = signal.Signals(signum).name
        print(f"\n[BOOT] Received {sig_name}. Starting graceful shutdown...")
        self.shutdown_requested = True
        self.shutdown_all_managers()
        self.cleanup()
        sys.exit(0)
        
    def _reload_handler(self, signum, frame):
        """Handle reload signal (SIGHUP)."""
        print(f"\n[BOOT] Received SIGHUP. Reloading configuration...")
        self.reload_configuration()
        
    def check_prerequisites(self):
        """Check all prerequisites before starting."""
        print("[BOOT] Checking prerequisites...")
        
        # Check Python interpreter
        if not os.path.exists(VENV_PYTHON_PATH):
            print(f"ERROR: Python interpreter not found at '{VENV_PYTHON_PATH}'.")
            print("Please ensure the virtual environment exists.")
            return False
            
        # Check database
        if not os.path.exists(DB_FULL_PATH):
            print(f"ERROR: Database not found at '{DB_FULL_PATH}'.")
            print("Please run init_database.py first.")
            return False
            
        # Check database tables
        try:
            conn = sqlite3.connect(DB_FULL_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM autorun_components LIMIT 1")
            cursor.execute("SELECT 1 FROM component_lifecycle_log LIMIT 1")
            conn.close()
        except sqlite3.Error as e:
            print(f"ERROR: Database tables not properly initialized: {e}")
            print("Please run init_database.py")
            return False
            
        # Create required directories
        for directory in [LOGS_DIR, os.path.dirname(PID_FILE)]:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as e:
                print(f"ERROR: Could not create directory '{directory}': {e}")
                return False
                
        print("[BOOT] All prerequisites satisfied.")
        return True
        
    def write_pid_file(self):
        """Write boot system PID to file."""
        try:
            with open(PID_FILE, 'w') as f:
                f.write(str(os.getpid()))
            print(f"[BOOT] Boot system PID written to {PID_FILE}")
        except IOError as e:
            print(f"[BOOT] Warning: Could not write PID file: {e}")
            
    def load_configuration(self):
        """Load configuration from file if it exists."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    # Override default configuration
                    if 'managers' in config:
                        MANAGER_CONFIG.update(config['managers'])
                print(f"[BOOT] Configuration loaded from {CONFIG_FILE}")
            except Exception as e:
                print(f"[BOOT] Warning: Could not load configuration: {e}")
                
    def launch_manager(self, manager_script: str, config: dict) -> Optional[subprocess.Popen]:
        """Launch a single manager process."""
        script_path = os.path.join(PROJECT_DIR, manager_script)
        
        if not os.path.exists(script_path):
            print(f"ERROR: Manager script '{manager_script}' not found at '{script_path}'.")
            return None
            
        print(f"[BOOT] Launching {config['name']}...")
        
        try:
            # Create dedicated log files
            log_base = manager_script.replace('.py', '')
            log_file = os.path.join(LOGS_DIR, f"{log_base}.log")
            err_file = os.path.join(LOGS_DIR, f"{log_base}.err")
            
            # Add timestamp to log files
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(log_file, 'a') as f:
                f.write(f"\n\n=== {config['name']} started at {timestamp} ===\n")
                
            process = subprocess.Popen(
                [VENV_PYTHON_PATH, script_path, 'autorun'],
                cwd=PROJECT_DIR,
                stdout=open(log_file, 'a'),
                stderr=open(err_file, 'a'),
                preexec_fn=os.setsid  # Create new process group
            )
            
            print(f"[BOOT] {config['name']} launched with PID: {process.pid}")
            
            # Log to database
            self.log_manager_event(manager_script, process.pid, 'MANAGER_STARTED')
            
            return process
            
        except Exception as e:
            print(f"ERROR launching {config['name']}: {e}")
            if config.get('critical', True):
                print(f"[BOOT] {config['name']} is critical. Aborting boot.")
                self.shutdown_all_managers()
                sys.exit(1)
            return None
            
    def log_manager_event(self, manager_script: str, pid: int, event_type: str):
        """Log manager events to database."""
        try:
            conn = sqlite3.connect(DB_FULL_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO component_lifecycle_log
                (component_id, process_pid, event_type, message, manager_script)
                VALUES (?, ?, ?, ?, ?)
            """, (f"boot_{manager_script}", pid, event_type, 
                  f"Boot system event for {manager_script}", "boot_system.py"))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print(f"[BOOT] Warning: Could not log to database: {e}")
            
    def check_manager_health(self, manager_script: str, process: subprocess.Popen) -> bool:
        """Check if a manager process is healthy."""
        # Basic check: is process still running?
        if process.poll() is not None:
            return False
            
        # TODO: Implement more sophisticated health checks
        # Could check:
        # - Manager responsiveness via IPC
        # - Component status via database
        # - Log file activity
        
        return True
        
    def monitor_managers(self):
        """Monitor manager health and restart if needed."""
        print("\n[BOOT] Entering monitoring mode. Press Ctrl+C to shutdown.")
        
        last_check = {}
        
        while not self.shutdown_requested:
            try:
                current_time = time.time()
                
                for manager_script, process in list(self.launched_managers.items()):
                    config = MANAGER_CONFIG[manager_script]
                    
                    # Check health at configured interval
                    if manager_script not in last_check or \
                       current_time - last_check[manager_script] > config['health_check_interval']:
                        
                        if not self.check_manager_health(manager_script, process):
                            print(f"\n[BOOT] {config['name']} appears to have crashed!")
                            
                            if config.get('critical', True):
                                print(f"[BOOT] {config['name']} is critical. Attempting restart...")
                                self.log_manager_event(manager_script, process.pid, 'MANAGER_CRASHED')
                                
                                # Attempt restart
                                del self.launched_managers[manager_script]
                                new_process = self.launch_manager(manager_script, config)
                                
                                if new_process:
                                    self.launched_managers[manager_script] = new_process
                                    print(f"[BOOT] {config['name']} restarted successfully.")
                                else:
                                    print(f"[BOOT] Failed to restart {config['name']}. System may be unstable.")
                            else:
                                print(f"[BOOT] {config['name']} is non-critical. Not restarting.")
                                del self.launched_managers[manager_script]
                                
                        last_check[manager_script] = current_time
                        
                time.sleep(5)  # Main monitoring loop interval
                
            except KeyboardInterrupt:
                print("\n[BOOT] Keyboard interrupt received.")
                self.shutdown_requested = True
                
    def shutdown_all_managers(self):
        """Shutdown all managers gracefully."""
        if not self.launched_managers:
            return
            
        print("\n[BOOT] Shutting down all managers...")
        
        # Send SIGTERM to all managers
        for manager_script, process in self.launched_managers.items():
            try:
                print(f"[BOOT] Sending SIGTERM to {MANAGER_CONFIG[manager_script]['name']} (PID: {process.pid})")
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                self.log_manager_event(manager_script, process.pid, 'MANAGER_SHUTDOWN_REQUESTED')
            except Exception as e:
                print(f"[BOOT] Error sending SIGTERM to {manager_script}: {e}")
                
        # Wait for graceful shutdown
        print("[BOOT] Waiting for managers to shutdown gracefully...")
        timeout = 30  # seconds
        start_time = time.time()
        
        while self.launched_managers and time.time() - start_time < timeout:
            for manager_script in list(self.launched_managers.keys()):
                process = self.launched_managers[manager_script]
                if process.poll() is not None:
                    print(f"[BOOT] {MANAGER_CONFIG[manager_script]['name']} shutdown complete.")
                    self.log_manager_event(manager_script, process.pid, 'MANAGER_STOPPED')
                    del self.launched_managers[manager_script]
            time.sleep(0.5)
            
        # Force kill any remaining
        if self.launched_managers:
            print("[BOOT] Some managers didn't shutdown gracefully. Forcing...")
            for manager_script, process in self.launched_managers.items():
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    print(f"[BOOT] Force killed {MANAGER_CONFIG[manager_script]['name']}")
                except:
                    pass
                    
    def cleanup(self):
        """Cleanup before exit."""
        if os.path.exists(PID_FILE):
            try:
                os.remove(PID_FILE)
            except:
                pass
                
        uptime = datetime.now() - self.start_time
        print(f"\n[BOOT] Boot system shutdown complete. Uptime: {uptime}")
        
    def reload_configuration(self):
        """Reload configuration and apply changes."""
        old_config = MANAGER_CONFIG.copy()
        self.load_configuration()
        
        # TODO: Implement configuration reload logic
        # - Compare old and new configurations
        # - Start new managers
        # - Stop removed managers
        # - Update existing manager configurations
        
        print("[BOOT] Configuration reload complete.")
        
    def run(self):
        """Main boot sequence."""
        print("=== n0m1_agi Boot System v2.0 ===")
        print(f"Starting at {self.start_time}")
        
        # Setup
        self.setup_signal_handlers()
        self.load_configuration()
        
        if not self.check_prerequisites():
            sys.exit(1)
            
        self.write_pid_file()
        
        # Launch managers
        print("\n[BOOT] Launching managers...")
        for manager_script, config in MANAGER_CONFIG.items():
            process = self.launch_manager(manager_script, config)
            if process:
                self.launched_managers[manager_script] = process
                
            # Startup delay
            if config.get('startup_delay', 0) > 0:
                time.sleep(config['startup_delay'])
                
        if not self.launched_managers:
            print("\n[BOOT] ERROR: No managers were successfully launched.")
            sys.exit(1)
            
        print(f"\n[BOOT] Successfully launched {len(self.launched_managers)} managers.")
        print("[BOOT] System components should now be starting via their managers.")
        print("[BOOT] Check logs in:")
        print(f"  - Manager logs: {LOGS_DIR}")
        print(f"  - Component logs: {os.path.join(PROJECT_DIR, 'logs')}")
        
        # Enter monitoring mode
        self.monitor_managers()
        
        # Cleanup on exit
        self.cleanup()

if __name__ == "__main__":
    boot_system = BootSystem()
    boot_system.run()
