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
├── temp_main_daemon.py        # CPU temperature daemon
├── cpu_usage_daemon.py        # CPU usage daemon
├── mem_usage_daemon.py        # Memory usage daemon
├── config.json               # Optional configuration
├── n0m1_agi.db              # SQLite database
├── logs/                     # Component logs
├── logs_managers/            # Manager logs
└── pids/                     # PID files

Dedicated daemons collect system metrics:
`temp_main_daemon.py` logs CPU temperature to `cpu_temperature_log`,
`cpu_usage_daemon.py` writes CPU usage to `cpu_usage_log`, and
`mem_usage_daemon.py` records memory usage in `memory_usage_log`.
Nano LLMs summarize each metric into tables like `cpu_temp_summary` which the
main LLM can then read.
Quick Start
1. Initial Setup
bash# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Tip: set N0M1_NATIVE=1 to run managers with the current Python interpreter
# rather than the virtual environment. Useful for testing without a venv.

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

# View recent system metrics
./n0m1_control.py metrics --limit 5
3. Component Management
bash# Enable a component
./n0m1_control.py enable cpu_usage_daemon

# Disable a component
./n0m1_control.py disable nano_analyzer_01

# View component logs
./n0m1_control.py logs cpu_usage_daemon

# Follow logs in real-time
./n0m1_control.py logs -f daemon_manager
Key Improvements
1. Fixed Critical Bugs

Fixed typo in temp_main_daemon.py (annce_startup → announce_startup)
Stop component logic is implemented for daemon_manager, but remains a
placeholder in other managers
Added proper error handling throughout
Added dedicated daemons for CPU temperature, usage and memory metrics

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
cpu_usage_log
memory_usage_log
Separate tables for CPU and memory usage collected by dedicated daemons
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

Using the LLM Processor and Nano Instances
-----------------------------------------
The repository includes example components for managing both a large language
model and many lightweight models:

* **llm_processor.py** – loaded by `main_llm_manager.py`. It uses the
  HuggingFace `transformers` library to pull down an open‑source model (default
  `distilgpt2`) and then enters an idle loop while logging lifecycle events.
* **nano_instance.py** – launched by `nano_manager.py`. Multiple instances can
  run in parallel by specifying `--instance_id` and `--model` arguments. The
  script can optionally pull recent rows from the `system_metrics_log` table and
  maintain a context window. LoRA weights and a system prompt file may be
  provided via `--lora` and `--system_prompt`.

To enable these components, insert or update records in the
`autorun_components` table with the appropriate `manager_affinity` and
`launch_args_json` values. When the system starts, the managers will read the
table and launch the configured models.

Configuring the Main LLM
------------------------
The tables ``llm_io_config`` and ``llm_notifications`` allow runtime control of
the main language model. ``llm_io_config`` stores comma-separated input tables
and the output table for each LLM. Set ``needs_reload`` to ``1`` to signal a
configuration change. The ``llm_config_daemon`` will write a ``CONFIG_RELOAD``
notification to ``llm_notifications`` which causes ``llm_processor`` to reload
its settings. A ``RUN`` notification instructs the processor to read from the
allowed tables and write results to the designated output table.

``llm_notifications`` also accepts an optional ``payload`` column. When
``llm_config_daemon`` inserts a ``CONFIG_RELOAD`` row the payload is ``NULL``,
but other daemons may send ``PUSH`` notifications with a comma separated list of
tables to read immediately. ``llm_processor`` treats a ``RUN`` or ``PUSH``
notification with a payload as an instruction to read those tables once and
store the results. A ``PULL_REQUEST`` notification causes the processor to write
``REQUEST:<payload>`` into its output table so external components can respond.
This mechanism allows other daemons to trigger ad-hoc runs or for the LLM to
ask for additional data while running.

LLM Command Daemon
------------------
The ``llm_command_daemon`` watches the ``llm_outputs`` table for rows whose
``content`` begins with ``CMD:``. A command like ``CMD:START nano_foo`` will
insert or update ``nano_foo`` in ``autorun_components`` with
``desired_state='active'``. This allows the language model to start other
components dynamically.

Run the daemon manually with ``python llm_command_daemon.py`` or add it to the
``autorun_components`` table so ``daemon_manager`` launches it automatically.

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
