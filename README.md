# Network Idle Monitor

Tracks screen lock idle time across Windows client PCs and displays
a live dashboard with daily summaries.

## Project Structure

```
network-idle-monitor/
├── agent.py              # Python agent — runs on each Windows PC
├── server.py             # Flask dashboard server — runs on Linux server
├── run_me.bat            # Runs agent.py with correct Python/venv path
├── schedule_task.bat     # Registers scheduled task (run once per PC as Admin)
├── download_packages.bat  # Downloads pip packages for offline install
├── .gitignore
└── README.md
```

## How It Works

```
Windows PC (locked)
    └── Scheduled Task "idlecounthours" (every 5 min, runs as SYSTEM)
            └── run_me.bat
                    └── agent.py
                            └── POSTs status to server

Linux Server
    └── server.py (Flask + PostgreSQL)
            └── Dashboard at http://SERVER_IP:12001
```

## Idle Detection

- **Idle** = screen is locked (`LogonUI.exe` is running)
- **Active** = screen is unlocked
- Works for any user — not tied to a specific login
- Daily idle total resets automatically at midnight

## Server Setup

### PostgreSQL

```bash
sudo apt install postgresql -y
sudo -u postgres psql
```

```sql
CREATE DATABASE idle_monitor;
CREATE USER idle_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE idle_monitor TO idle_user;
GRANT USAGE ON SCHEMA public TO idle_user;
GRANT CREATE ON SCHEMA public TO idle_user;
\q
```

### Run Server

```bash
pip3 install flask psycopg2-binary --break-system-packages
python3 server.py
```

Edit `server.py` and set your DB credentials:
```python
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "idle_monitor"
DB_USER = "idle_user"
DB_PASS = "your_password"
```

## Client Setup (Windows PC)

### Prerequisites
- Python installed at `c:\streamline\python-3.10.10\`
- `.venv` folder with `requests` installed at `C:\IdleAgent\.venv\`

### Quick Install (run once as Administrator)

```bat
schedule_task.bat
```

This registers the `idlecounthours` scheduled task that runs `run_me.bat`
every 5 minutes as SYSTEM — no user login required.

### Verify

```bat
schtasks /query /tn "idlecounthours"
type C:\IdleAgent\agent.log
```

## Client Deployment at Scale

### Via Ansible

```bash
# Edit inventory.ini with your PC hostnames and credentials
ansible-playbook deploy_agent.yml -i inventory.ini

# Stop on all PCs
ansible-playbook stop_agent.yml -i inventory.ini
```

### Via PowerShell (stop/start across OU)

```powershell
# Stop all
Get-ADComputer -Filter * -SearchBase "OU=Computers,DC=yourdomain,DC=com" | ForEach-Object {
    Invoke-Command -ComputerName $_.Name -ScriptBlock {
        Disable-ScheduledTask -TaskName "idlecounthours"
    } -ErrorAction SilentlyContinue
}

# Start all
Get-ADComputer -Filter * -SearchBase "OU=Computers,DC=yourdomain,DC=com" | ForEach-Object {
    Invoke-Command -ComputerName $_.Name -ScriptBlock {
        Enable-ScheduledTask -TaskName "idlecounthours"
        Start-ScheduledTask  -TaskName "idlecounthours"
    } -ErrorAction SilentlyContinue
}
```

## Dashboard

| Tab | Shows |
|---|---|
| Live Status | Current idle/active status per machine |
| Day-wise Summary | Total systems / idle / active / total idle hours per day |
| Per System History | Each machine's idle time per day |

## Notes

- `inventory.ini` is excluded from Git (contains passwords) — create locally
- `.venv/` and `venv.zip` are excluded — create using `create_venv_bundle.bat`
- `*.db`, `*.log`, `state.json` are excluded — runtime data, machine-specific

## Requirements

See `requirements.txt` for all dependencies.

### Server (Linux)
```bash
pip3 install -r requirements.txt --break-system-packages
```

### Client (Windows) — with internet
```bat
python -m pip install -r requirements.txt
```

### Client (Windows) — offline
```bat
python -m pip install -r requirements.txt --no-index --find-links=packages
```

### Requirements breakdown

| Package | Version | Used by |
|---|---|---|
| flask | 3.0.0 | Server — web framework |
| psycopg2-binary | 2.9.9 | Server — PostgreSQL connector |
| requests | 2.31.0 | Client agent — HTTP POST to server |
| certifi | 2024.2.2 | requests dependency |
| charset-normalizer | 3.3.2 | requests dependency |
| idna | 3.6 | requests dependency |
| urllib3 | 2.2.1 | requests dependency |
