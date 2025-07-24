#!/usr/bin/env python3
"""
Database initialization script for n0m1_agi system.
Creates all required tables and populates initial configuration.
"""
import sqlite3
import os
import json
from datetime import datetime

# --- Configuration ---
DB_FILE_NAME = 'n0m1_agi.db'
DB_FULL_PATH = os.path.expanduser(f'~/n0m1_agi/{DB_FILE_NAME}')
# --- End Configuration ---

def create_database_schema():
    """Create all required tables for the n0m1_agi system."""
    conn = None
    try:
        # Ensure directory exists
        db_dir = os.path.dirname(DB_FULL_PATH)
        os.makedirs(db_dir, exist_ok=True)
        
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        
        # Enable foreign keys
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        print("Creating database schema...")
        
        # 1. autorun_components table - Configuration for all managed components
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS autorun_components (
                component_id TEXT PRIMARY KEY,
                base_script_name TEXT NOT NULL,
                manager_affinity TEXT NOT NULL,
                desired_state TEXT NOT NULL DEFAULT 'active' CHECK (desired_state IN ('active', 'inactive')),
                launch_args_json TEXT DEFAULT '{}',
                run_type_on_boot TEXT DEFAULT 'PRIMARY_RUN',
                description TEXT,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # 2. component_lifecycle_log table - Lifecycle events for all components
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS component_lifecycle_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                component_id TEXT NOT NULL,
                process_pid INTEGER,
                event_type TEXT NOT NULL,
                run_type TEXT,
                message TEXT,
                manager_script TEXT,
                script_path TEXT
            );
        """)
        
        # 3. cpu_temperature_log table - For temp_main_daemon data
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cpu_temperature_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                temperature_celsius REAL NOT NULL
            );
        """)

        # 4. system_metrics_log table - Generic system metrics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_metrics_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                cpu_temp REAL,
                cpu_usage REAL NOT NULL,
                mem_usage REAL NOT NULL
            );
        """)

        # 5. nano_outputs table - Stores nano instance generated summaries
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS nano_outputs (
                id INTEGER PRIMARY KEY,
                nano_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                content TEXT
            );
            """
        )

        # 6. llm_outputs table - Stores LLM processor generated outputs
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_outputs (
                id INTEGER PRIMARY KEY,
                llm_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                content TEXT
            );
            """
        )
        
        # Create indexes for better performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ac_manager ON autorun_components (manager_affinity);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ac_state ON autorun_components (desired_state);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cll_component_id ON component_lifecycle_log (component_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cll_event_type ON component_lifecycle_log (event_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cll_timestamp ON component_lifecycle_log (event_timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_temp_timestamp ON cpu_temperature_log (timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON system_metrics_log (timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_out_timestamp ON llm_outputs (timestamp);")

        # 7. llm_io_config table - runtime configuration for LLM processors
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS llm_io_config (
                llm_id TEXT PRIMARY KEY,
                read_tables TEXT NOT NULL,
                output_table TEXT NOT NULL,
                needs_reload INTEGER DEFAULT 0
            );
        """)

        # 8. llm_notifications table - push style notifications for LLMs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS llm_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                llm_id TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                payload TEXT,
                processed INTEGER DEFAULT 0,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 8. db_access_log table - tracks table read/write events
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS db_access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                component_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                access_type TEXT NOT NULL
            );
            """
        )
        
        # Create trigger to update modified_timestamp
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_autorun_timestamp 
            AFTER UPDATE ON autorun_components
            FOR EACH ROW
            BEGIN
                UPDATE autorun_components SET modified_timestamp = CURRENT_TIMESTAMP 
                WHERE component_id = NEW.component_id;
            END;
        """)
        
        conn.commit()
        print("Database schema created successfully.")
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.close()

def populate_default_components():
    """Populate the autorun_components table with default component configurations."""
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        
        print("\nPopulating default component configurations...")
        
        # Default components configuration
        default_components = [
            {
                'component_id': 'temp_main_daemon',
                'base_script_name': 'temp_main_daemon.py',
                'manager_affinity': 'daemon_manager',
                'desired_state': 'active',
                'launch_args_json': '{}',
                'run_type_on_boot': 'PRIMARY_RUN',
                'description': 'CPU temperature monitoring daemon'
            },
            {
                'component_id': 'system_metrics_daemon',
                'base_script_name': 'system_metrics_daemon.py',
                'manager_affinity': 'daemon_manager',
                'desired_state': 'active',
                'launch_args_json': '{}',
                'run_type_on_boot': 'PRIMARY_RUN',
                'description': 'Cross-platform system metrics collector'
            },
            {
                'component_id': 'main_llm_processor',
                'base_script_name': 'llm_processor.py',
                'manager_affinity': 'main_llm_manager',
                'desired_state': 'inactive',  # Set to inactive by default
                'launch_args_json': '{"--model": "default", "--threads": "4"}',
                'run_type_on_boot': 'PRIMARY_RUN',
                'description': 'Main LLM processing component'
            },
            {
                'component_id': 'llm_config_daemon',
                'base_script_name': 'llm_config_daemon.py',
                'manager_affinity': 'daemon_manager',
                'desired_state': 'active',
                'launch_args_json': '{}',
                'run_type_on_boot': 'PRIMARY_RUN',
                'description': 'Daemon that manages LLM configuration notifications'
            },
            # Example nano instances (inactive by default)
            {
                'component_id': 'nano_analyzer_01',
                'base_script_name': 'nano_instance.py',
                'manager_affinity': 'nano_manager',
                'desired_state': 'inactive',
                'launch_args_json': '{"--instance_id": "analyzer_01", "--mode": "analysis"}',
                'run_type_on_boot': 'PRIMARY_RUN',
                'description': 'Nano instance for data analysis'
            },
            {
                'component_id': 'nano_collector_01',
                'base_script_name': 'nano_instance.py',
                'manager_affinity': 'nano_manager',
                'desired_state': 'inactive',
                'launch_args_json': '{"--instance_id": "collector_01", "--mode": "collection"}',
                'run_type_on_boot': 'PRIMARY_RUN',
                'description': 'Nano instance for data collection'
            }
        ]
        
        for component in default_components:
            # Use INSERT OR IGNORE to avoid duplicates
            cursor.execute("""
                INSERT OR IGNORE INTO autorun_components
                (component_id, base_script_name, manager_affinity, desired_state, 
                 launch_args_json, run_type_on_boot, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                component['component_id'],
                component['base_script_name'],
                component['manager_affinity'],
                component['desired_state'],
                component['launch_args_json'],
                component['run_type_on_boot'],
                component['description']
            ))
            
            if cursor.rowcount > 0:
                print(f"  Added component: {component['component_id']} ({component['desired_state']})")
            else:
                print(f"  Component already exists: {component['component_id']}")

        # Insert default LLM IO configuration
        cursor.execute(
            """
            INSERT OR IGNORE INTO llm_io_config (llm_id, read_tables, output_table, needs_reload)
            VALUES ('main_llm_processor', 'system_metrics_log', 'llm_outputs', 0)
            """
        )
        if cursor.rowcount > 0:
            print("  Added llm_io_config for main_llm_processor")

        conn.commit()
        print("\nDefault components populated successfully.")
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.close()

def show_current_configuration():
    """Display current component configuration."""
    conn = None
    try:
        conn = sqlite3.connect(DB_FULL_PATH)
        cursor = conn.cursor()
        
        print("\n" + "="*80)
        print("CURRENT COMPONENT CONFIGURATION")
        print("="*80)
        
        cursor.execute("""
            SELECT component_id, base_script_name, manager_affinity, 
                   desired_state, launch_args_json, description
            FROM autorun_components
            ORDER BY manager_affinity, component_id
        """)
        
        current_manager = None
        for row in cursor.fetchall():
            comp_id, script, manager, state, args, desc = row
            
            if manager != current_manager:
                print(f"\n[{manager}]")
                current_manager = manager
            
            status_marker = "✓" if state == "active" else "✗"
            print(f"  {status_marker} {comp_id}: {desc or 'No description'}")
            print(f"     Script: {script}")
            if args and args != '{}':
                print(f"     Args: {args}")
        
        print("\n" + "="*80)
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

def main():
    """Main function to initialize the database."""
    print("n0m1_agi Database Initialization")
    print("-" * 40)
    
    # Check if database already exists
    if os.path.exists(DB_FULL_PATH):
        response = input(f"\nDatabase already exists at {DB_FULL_PATH}.\nDo you want to:\n  [1] Add missing tables only (recommended)\n  [2] Reset database completely (WARNING: data loss)\n  [3] Cancel\n\nChoice (1/2/3): ")
        
        if response == '3':
            print("Cancelled.")
            return
        elif response == '2':
            confirm = input("Are you sure? This will DELETE all existing data! Type 'yes' to confirm: ")
            if confirm.lower() == 'yes':
                os.remove(DB_FULL_PATH)
                print("Database deleted.")
            else:
                print("Cancelled.")
                return
    
    # Create schema
    create_database_schema()
    
    # Populate defaults
    populate_default_components()
    
    # Show configuration
    show_current_configuration()
    
    print("\nDatabase initialization complete!")
    print(f"Database location: {DB_FULL_PATH}")
    print("\nNext steps:")
    print("1. Review and modify component configurations as needed")
    print("2. Run ./boot_system.py to start the system")

if __name__ == "__main__":
    main()
