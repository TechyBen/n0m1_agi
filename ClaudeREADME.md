Overview
The n0m1_agi framework is a robust process management system designed to manage multiple components including daemons, nano instances, and LLM processors. The system features automatic startup, health monitoring, graceful shutdown, and comprehensive logging.
System Architecture
n0m1_agi/
├── boot_system_enhanced.py    # Main boot system with monitoring
├── daemon_manager.py          # Manages daemon components
├── nano_manager.py            # Manages nano instances  
├── main_llm_manager.py        # Manages LLM processor
├── manager_utils.py           # Shared utilities
├── init_database.py           # Database initialization
├── n0m1_control.py           # System control interface
├── temp_main_daemon.py        # Example daemon (fixed)
├── config.json               # Optional configuration
├── n0m1_agi.db              # SQLite database
├── logs/                     # Component logs
├── logs_managers/            # Manager logs
└── pids/                     # PID files
Quick Start
1. Initial Setup
bash# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (if any)
# pip install -r requirements.txt

# Initialize database
./init_database.py
2. System Control
bash# Make control script executable
chmod +x n0m1_control.py

# Start the system
./n0m1_control.py start

# Check status
./n0m1_control.py status

# View detailed status
./n0m1_control.py status --detailed

# Stop the system
./n0m1_control.py stop

# Restart the system
./n0m1_control.py restart
3. Component Management
bash# Enable a component
./n0m1_control.py enable temp_main_daemon

# Disable a component
./n0m1_control.py disable nano_analyzer_01

# View component logs
./n0m1_control.py logs temp_main_daemon

# Follow logs in real-time
./n0m1_control.py logs -f daemon_manager
Key Improvements
1. Fixed Critical Bugs

Fixed typo in temp_main_daemon.py (annce_startup → announce_startup)
Completed implementation of stop_component() functions
Added proper error handling throughout

2. Database Management

Complete database schema with all required tables
Initialization script with default configurations
Proper indexing for performance
Lifecycle event logging

3. Enhanced Boot System

Signal handling for graceful shutdown (SIGTERM, SIGINT)
Health monitoring with automatic restart for critical managers
Configuration file support
Comprehensive logging

4. Unified Control Interface

Single command-line interface for all operations
Component enable/disable functionality
Log viewing and following
Detailed status reporting

5. Shared Utilities

Centralized process management functions
Consistent PID file handling
Database logging utilities
Health check framework

Database Schema
autorun_components
Stores component configurations:

component_id: Unique identifier
base_script_name: Python script to execute
manager_affinity: Which manager controls this component
desired_state: 'active' or 'inactive'
launch_args_json: JSON arguments for the component
run_type_on_boot: Run type identifier

component_lifecycle_log
Tracks all lifecycle events:

Component starts/stops
Errors and crashes
Manager events
Timestamps and PIDs

cpu_temperature_log
Example data table for the temperature daemon
Configuration
Optional config.json
json{
  "managers": {
    "daemon_manager.py": {
      "name": "Daemon Manager",
      "startup_delay": 1,
      "health_check_interval": 30,
      "critical": true
    }
  }
}
Troubleshooting
System Won't Start

Check database initialization: ls -la ~/n0m1_agi/n0m1_agi.db
Verify virtual environment: which python
Check boot system logs: tail -f logs_managers/boot_system_daemon.log

Component Not Running

Check component status: ./n0m1_control.py status --detailed
Verify component is enabled in database
Check component logs: ./n0m1_control.py logs [component_id]
Check manager logs for the component's manager

Database Issues

Re-initialize (preserves data): ./init_database.py
Check permissions: ls -la ~/n0m1_agi/
Verify tables: sqlite3 ~/n0m1_agi/n0m1_agi.db ".tables"

Development
Adding New Components

Create your component script
Add to database:

sqlINSERT INTO autorun_components 
(component_id, base_script_name, manager_affinity, desired_state, launch_args_json)
VALUES ('my_component', 'my_script.py', 'daemon_manager', 'active', '{}');

Restart the appropriate manager or system

Custom Managers
Managers should:

Read from autorun_components table
Implement start/stop/status functions
Log to component_lifecycle_log
Handle signals gracefully
Use shared utilities from manager_utils.py

Best Practices

Always use the control script for system management
Check logs when debugging issues
Use lifecycle logging in your components
Handle signals in long-running processes
Test components individually before adding to autorun

Security Notes

PID files are stored locally and should be protected
Database contains system configuration
Log files may contain sensitive information
Consider file permissions in production

Future Enhancements

 Web interface for monitoring
 Remote management capabilities
 Metrics collection and graphing
 Alert system for failures
 Automatic log rotation
 Component dependency management
 Resource usage monitoring
 Configuration hot-reload
