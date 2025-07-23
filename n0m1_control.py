#!/usr/bin/env python3
"""
Control script for n0m1_agi system.
Provides unified interface for system management.
"""
import os
import sys
import signal
import time
import subprocess
import sqlite3
import json
import argparse
from manager_utils import get_venv_python
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

# --- Configuration ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON_PATH = get_venv_python(PROJECT_DIR)
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
BOOT_PID_FILE = os.path.join(PROJECT_DIR, "pids", "boot_system.pid")
BOOT_SCRIPT = os.path.join(PROJECT_DIR, "boot_system_enhanced.py")
# --- End Configuration ---

class SystemController:
    def __init__(self):
        self.db_path = DB_FULL_PATH
        
    def is_boot_system_running(self) -> Tuple[bool, Optional[int]]:
        """Check if boot system is running."""
        if not os.path.exists(BOOT_PID_FILE):
            return False, None
            
        try:
            with open(BOOT_PID_FILE, 'r') as f:
                pid = int(f.read().strip())
                
            # Check if process is running
            os.kill(pid, 0)
            return True, pid
        except (ValueError, IOError, OSError):
            return False, None
            
    def start_system(self) -> bool:
        """Start the n0m1_agi system."""
        running, pid = self.is_boot_system_running()
        if running:
            print(f"System is already running (Boot PID: {pid})")
            return False
            
        print("Starting n0m1_agi system...")
        
        # Check prerequisites
        if not os.path.exists(DB_FULL_PATH):
            print("ERROR: Database not initialized. Please run:")
            print(f"  {VENV_PYTHON_PATH} init_database.py")
            return False
            
        # Start boot system
        try:
            log_file = os.path.join(PROJECT_DIR, "logs_managers", "boot_system_daemon.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            with open(log_file, 'a') as f:
                f.write(f"\n\n=== Boot system started at {datetime.now()} ===\n")
                
            process = subprocess.Popen(
                [VENV_PYTHON_PATH, BOOT_SCRIPT],
                cwd=PROJECT_DIR,
                stdout=open(log_file, 'a'),
                stderr=subprocess.STDOUT,
                start_new_session=True  # Detach from terminal
            )
            
            # Wait a moment and check if it started
            time.sleep(3)
            running, pid = self.is_boot_system_running()
            
            if running:
                print(f"System started successfully (Boot PID: {pid})")
                print(f"Logs: {log_file}")
                return True
            else:
                print("ERROR: System failed to start. Check logs.")
                return False
                
        except Exception as e:
            print(f"ERROR starting system: {e}")
            return False
            
    def stop_system(self, force: bool = False) -> bool:
        """Stop the n0m1_agi system."""
        running, pid = self.is_boot_system_running()
        if not running:
            print("System is not running.")
            return False
            
        print(f"Stopping n0m1_agi system (Boot PID: {pid})...")
        
        try:
            if force:
                os.kill(pid, signal.SIGKILL)
                print("System forcefully stopped.")
            else:
                os.kill(pid, signal.SIGTERM)
                print("Shutdown signal sent. Waiting for graceful shutdown...")
                
                # Wait for shutdown
                timeout = 60
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if not self.is_boot_system_running()[0]:
                        print("System stopped successfully.")
                        return True
                    time.sleep(1)
                    
                print("System didn't stop gracefully. Use --force to force stop.")
                return False
                
        except Exception as e:
            print(f"ERROR stopping system: {e}")
            return False
            
    def restart_system(self) -> bool:
        """Restart the n0m1_agi system."""
        print("Restarting n0m1_agi system...")
        
        if self.is_boot_system_running()[0]:
            if not self.stop_system():
                print("Failed to stop system.")
                return False
                
        time.sleep(2)
        return self.start_system()
        
    def get_system_status(self) -> Dict:
        """Get comprehensive system status."""
        status = {
            'boot_system': {'running': False, 'pid': None},
            'managers': {},
            'components': {},
            'errors': []
        }
        
        # Check boot system
        running, pid = self.is_boot_system_running()
        status['boot_system']['running'] = running
        status['boot_system']['pid'] = pid
        
        # Check database connection
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get manager status from recent lifecycle events
            cursor.execute("""
                SELECT DISTINCT component_id, process_pid, event_type, event_timestamp
                FROM component_lifecycle_log
                WHERE component_id LIKE 'boot_%'
                  AND event_timestamp > datetime('now', '-1 hour')
                ORDER BY event_timestamp DESC
            """)
            
            for comp_id, pid, event_type, timestamp in cursor.fetchall():
                manager_name = comp_id.replace('boot_', '')
                if manager_name not in status['managers']:
                    status['managers'][manager_name] = {
                        'last_event': event_type,
                        'last_event_time': timestamp,
                        'pid': pid
                    }
                    
            # Get component status
            cursor.execute("""
                SELECT 
                    ac.component_id,
                    ac.desired_state,
                    ac.manager_affinity,
                    cl.event_type,
                    cl.event_timestamp,
                    cl.process_pid
                FROM autorun_components ac
                LEFT JOIN (
                    SELECT component_id, event_type, event_timestamp, process_pid,
                           ROW_NUMBER() OVER (PARTITION BY component_id ORDER BY event_timestamp DESC) as rn
                    FROM component_lifecycle_log
                    WHERE event_timestamp > datetime('now', '-1 hour')
                ) cl ON ac.component_id = cl.component_id AND cl.rn = 1
                ORDER BY ac.manager_affinity, ac.component_id
            """)
            
            for comp_id, desired_state, manager, event_type, timestamp, pid in cursor.fetchall():
                status['components'][comp_id] = {
                    'desired_state': desired_state,
                    'manager': manager,
                    'last_event': event_type or 'NO_RECENT_EVENTS',
                    'last_event_time': timestamp,
                    'pid': pid
                }
                
            # Check for recent errors
            cursor.execute("""
                SELECT component_id, event_type, message, event_timestamp
                FROM component_lifecycle_log
                WHERE event_type IN ('ERROR', 'CRITICAL_ERROR', 'STOP_FAILED', 'MANAGER_CRASHED')
                  AND event_timestamp > datetime('now', '-1 hour')
                ORDER BY event_timestamp DESC
                LIMIT 10
            """)
            
            for comp_id, event_type, message, timestamp in cursor.fetchall():
                status['errors'].append({
                    'component': comp_id,
                    'type': event_type,
                    'message': message,
                    'time': timestamp
                })
                
            conn.close()
            
        except sqlite3.Error as e:
            status['errors'].append({
                'component': 'database',
                'type': 'DB_ERROR',
                'message': str(e),
                'time': datetime.now().isoformat()
            })
            
        return status
        
    def show_status(self, detailed: bool = False):
        """Display system status."""
        status = self.get_system_status()
        
        print("\n" + "="*80)
        print("n0m1_agi SYSTEM STATUS")
        print("="*80)
        
        # Boot system status
        if status['boot_system']['running']:
            print(f"\n✓ Boot System: RUNNING (PID: {status['boot_system']['pid']})")
        else:
            print("\n✗ Boot System: STOPPED")
            
        # Manager status
        if status['managers']:
            print("\nManagers:")
            for manager, info in sorted(status['managers'].items()):
                symbol = "✓" if 'STARTED' in info.get('last_event', '') else "?"
                print(f"  {symbol} {manager}: {info.get('last_event', 'UNKNOWN')}")
                if detailed:
                    print(f"     PID: {info.get('pid')}, Last update: {info.get('last_event_time')}")
                    
        # Component status
        if status['components']:
            print("\nComponents:")
            current_manager = None
            for comp_id, info in sorted(status['components'].items(), key=lambda x: (x[1]['manager'], x[0])):
                if info['manager'] != current_manager:
                    current_manager = info['manager']
                    print(f"\n  [{current_manager}]")
                    
                # Determine status symbol
                if info['desired_state'] == 'inactive':
                    symbol = "○"  # Inactive
                elif info['last_event'] in ['STARTED_SUCCESSFULLY', 'START_ATTEMPT']:
                    symbol = "✓"  # Running
                elif info['last_event'] in ['STOPPED_SUCCESSFULLY', 'NO_RECENT_EVENTS']:
                    symbol = "✗"  # Stopped
                else:
                    symbol = "⚠"  # Warning/Error
                    
                print(f"    {symbol} {comp_id}: {info['last_event']}")
                if detailed:
                    print(f"       Desired: {info['desired_state']}, PID: {info.get('pid')}")
                    
        # Recent errors
        if status['errors']:
            print(f"\n⚠ Recent Errors ({len(status['errors'])})")
            for error in status['errors'][:5]:  # Show max 5
                print(f"  - {error['component']}: {error['type']} - {error.get('message', 'No message')}")
                
        print("\n" + "="*80)
        
    def show_logs(self, component: Optional[str] = None, lines: int = 50, follow: bool = False):
        """Show logs for components."""
        log_dir = os.path.join(PROJECT_DIR, 'logs')
        manager_log_dir = os.path.join(PROJECT_DIR, 'logs_managers')
        
        if component:
            # Show specific component logs
            log_files = []
            
            # Check component logs
            comp_log = os.path.join(log_dir, f"{component}.log")
            if os.path.exists(comp_log):
                log_files.append(comp_log)
                
            # Check manager logs
            manager_log = os.path.join(manager_log_dir, f"{component}.log")
            if os.path.exists(manager_log):
                log_files.append(manager_log)
                
            if not log_files:
                print(f"No log files found for component: {component}")
                return
                
            for log_file in log_files:
                print(f"\n=== {log_file} ===")
                if follow:
                    subprocess.run(['tail', '-f', log_file])
                else:
                    subprocess.run(['tail', '-n', str(lines), log_file])
        else:
            # List all available logs
            print("\nAvailable log files:")
            
            print("\nComponent logs:")
            if os.path.exists(log_dir):
                for log_file in sorted(os.listdir(log_dir)):
                    if log_file.endswith('.log'):
                        print(f"  - {log_file}")
                        
            print("\nManager logs:")
            if os.path.exists(manager_log_dir):
                for log_file in sorted(os.listdir(manager_log_dir)):
                    if log_file.endswith('.log'):
                        print(f"  - {log_file}")
                        
    def manage_component(self, action: str, component_id: str):
        """Manage individual components."""
        # Get component configuration
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT manager_affinity, desired_state
                FROM autorun_components
                WHERE component_id = ?
            """, (component_id,))
            
            result = cursor.fetchone()
            if not result:
                print(f"Component '{component_id}' not found.")
                return False
                
            manager, current_state = result
            
            if action == 'enable':
                cursor.execute("""
                    UPDATE autorun_components
                    SET desired_state = 'active'
                    WHERE component_id = ?
                """, (component_id,))
                conn.commit()
                print(f"Component '{component_id}' enabled.")
                
            elif action == 'disable':
                cursor.execute("""
                    UPDATE autorun_components
                    SET desired_state = 'inactive'
                    WHERE component_id = ?
                """, (component_id,))
                conn.commit()
                print(f"Component '{component_id}' disabled.")
                
            conn.close()
            
            # If system is running, apply changes
            if self.is_boot_system_running()[0]:
                print(f"Triggering {manager} to apply changes...")
                # Could send signal to specific manager or restart it
                
            return True
            
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return False

    def show_metrics(self, limit: int = 10):
        """Display recent system metrics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp, cpu_usage, mem_usage, cpu_temp "
                "FROM system_metrics_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                print("No metrics data available.")
                return

            cpu_vals = [r[1] for r in rows if r[1] is not None]
            mem_vals = [r[2] for r in rows if r[2] is not None]
            temp_vals = [r[3] for r in rows if r[3] is not None]

            last = rows[0]
            print("\nRecent System Metrics:\n")
            print(f" Last update : {last[0]}")
            if cpu_vals:
                avg_cpu = sum(cpu_vals) / len(cpu_vals)
                print(f" CPU usage   : last {last[1]}% | avg {avg_cpu:.1f}%")
            if mem_vals:
                avg_mem = sum(mem_vals) / len(mem_vals)
                print(f" Memory usage: last {last[2]}% | avg {avg_mem:.1f}%")
            if temp_vals:
                avg_temp = sum(temp_vals) / len(temp_vals)
                print(f" CPU temp    : last {last[3]}°C | avg {avg_temp:.1f}°C")
            else:
                print(" CPU temp    : N/A")
        except sqlite3.Error as e:
            print(f"Database error: {e}")

def main():
    parser = argparse.ArgumentParser(
        description='n0m1_agi System Control',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s start                    # Start the system
  %(prog)s stop                     # Stop the system gracefully
  %(prog)s restart                  # Restart the system
  %(prog)s status                   # Show system status
  %(prog)s status --detailed        # Show detailed status
  %(prog)s logs                     # List available logs
  %(prog)s logs temp_main_daemon    # Show specific component logs
  %(prog)s logs -f boot_system      # Follow logs in real-time
  %(prog)s enable temp_main_daemon  # Enable a component
  %(prog)s disable nano_analyzer_01 # Disable a component
  %(prog)s metrics --limit 5        # Show recent system metrics
        """
    )
    
    parser.add_argument('command',
                       choices=['start', 'stop', 'restart', 'status', 'logs', 'enable', 'disable', 'metrics'],
                       help='Command to execute')
    parser.add_argument('component', nargs='?', help='Component ID (for logs/enable/disable)')
    parser.add_argument('-f', '--follow', action='store_true', help='Follow log output')
    parser.add_argument('-n', '--lines', type=int, default=50, help='Number of log lines to show')
    parser.add_argument('--limit', type=int, default=10,
                        help='Number of recent metric rows to read')
    parser.add_argument('--force', action='store_true', help='Force stop')
    parser.add_argument('--detailed', action='store_true', help='Show detailed status')
    
    args = parser.parse_args()
    controller = SystemController()
    
    if args.command == 'start':
        controller.start_system()
        
    elif args.command == 'stop':
        controller.stop_system(force=args.force)
        
    elif args.command == 'restart':
        controller.restart_system()
        
    elif args.command == 'status':
        controller.show_status(detailed=args.detailed)
        
    elif args.command == 'logs':
        controller.show_logs(
            component=args.component,
            lines=args.lines,
            follow=args.follow
        )

    elif args.command == 'metrics':
        controller.show_metrics(limit=args.limit)

    elif args.command in ['enable', 'disable']:
        if not args.component:
            print(f"ERROR: Component ID required for {args.command}")
            sys.exit(1)
        controller.manage_component(args.command, args.component)

if __name__ == "__main__":
    main()
