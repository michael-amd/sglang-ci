# SGLang CI Dashboard - Implementation Summary

## Overview

A comprehensive web-based dashboard for viewing CI results, performance plots, and trends across MI30X and MI35X hardware platforms. The dashboard fetches data from the GitHub `log` branch, making it accessible even from machines behind firewalls (like mi35x).

## Problem Solved

**Original Issue**: mi35x machines are behind a firewall and cannot be accessed from outside, making it difficult to host a dashboard server that can be accessed externally.

**Solution**: Instead of hosting the server on mi35x and trying to access it externally, the dashboard:
1. Fetches all logs and data from the GitHub `log` branch (uploaded by `github_log_upload.sh`)
2. Can be hosted anywhere (even on a developer's laptop or a public-facing server)
3. Automatically falls back to local filesystem if GitHub is unavailable

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CI Workflow                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Nightly CI runs on mi30x/mi35x                          │
│     └─ Generates logs locally                               │
│                                                              │
│  2. github_log_upload.sh uploads logs to GitHub             │
│     └─ Pushes to log branch (automatic via cron)            │
│                                                              │
│  3. Dashboard fetches from GitHub                            │
│     ├─ No direct access to mi30x/mi35x needed               │
│     ├─ Works from anywhere (laptop, public server, etc.)    │
│     └─ Falls back to local files if available               │
│                                                              │
└─────────────────────────────────────────────────────────────┘

GitHub Repository (log branch)
├── cron_log/
│   ├── mi30x/
│   │   └── 20251106/
│   │       ├── grok_nightly.log
│   │       ├── deepseek_dp_attention.log
│   │       └── ...
│   └── mi35x/
│       └── 20251106/
│           └── ...
├── plot/
│   ├── mi30x/
│   │   ├── GROK1/online/
│   │   ├── GROK2/online/
│   │   └── DeepSeek-V3-0324/online/
│   └── mi35x/
│       └── ...
└── test/
    └── sanity_check_log/
        ├── mi30x/
        └── mi35x/
```

## Key Components

### 1. Main Application (`app.py`)
- Flask web server with REST API
- Routes for dashboard views and API endpoints
- Configurable to use GitHub or local data source
- Default: GitHub mode with local fallback

### 2. GitHub Data Collector (`github_data_collector.py`)
- Fetches logs from GitHub raw URLs
- Uses GitHub API to list directories
- Parses logs directly from GitHub
- Falls back to local filesystem if GitHub unavailable
- Connection pooling for efficient requests

### 3. Local Data Collector (`data_collector.py`)
- Original implementation using local filesystem
- Reuses logic from `send_daily_summary_alert.py`
- Used as fallback when GitHub is unavailable

### 4. Web Interface
- **Home Page**: Summary for both hardware platforms
- **Hardware Views**: Detailed results per platform
- **Trends Page**: Historical charts and analytics
- **Plots Viewer**: Performance benchmark plots
- Responsive design (Bootstrap 5)
- Interactive charts (Chart.js)

### 5. REST API
- `/api/summary/<hardware>/<date>` - Daily summary
- `/api/trends/<hardware>?days=N` - Historical trends
- `/api/dates/<hardware>` - Available dates
- `/api/plots/<hardware>/<date>` - Plot URLs
- `/api/compare?date=YYYYMMDD` - Compare hardware
- `/health` - Health check

## Files Created

```
dashboard/
├── app.py                          # Main Flask application
├── data_collector.py               # Local data collector
├── github_data_collector.py        # GitHub data collector (NEW!)
├── requirements.txt                # Python dependencies
├── README.md                       # Comprehensive documentation
├── DASHBOARD_SUMMARY.md           # This file
├── start_dashboard.sh              # Startup script
├── stop_dashboard.sh               # Stop script
├── .gitignore                      # Git ignore rules
├── templates/                      # HTML templates
│   ├── base.html                  # Base template
│   ├── index.html                 # Home page
│   ├── hardware.html              # Hardware-specific view
│   ├── trends.html                # Trends page
│   ├── plots.html                 # Plots viewer
│   ├── 404.html                   # 404 error
│   └── 500.html                   # 500 error
└── static/                         # Static assets
    ├── css/
    │   └── style.css              # Custom CSS
    └── js/
        └── dashboard.js           # JavaScript utilities
```

## Usage

### Quick Start (Default: GitHub Mode)

```bash
cd /mnt/raid/michael/sglang-ci/dashboard

# Install dependencies
pip install -r requirements.txt

# Start dashboard (fetches from GitHub)
python app.py

# Or use startup script
bash start_dashboard.sh
```

Then open browser to: http://127.0.0.1:5000

### Deployment Options

**Option 1: Developer Laptop**
```bash
# Dashboard on laptop, data from GitHub
cd /mnt/raid/michael/sglang-ci/dashboard
python app.py --host 0.0.0.0 --port 5000
# Access from: http://laptop-ip:5000
```

**Option 2: Public Server**
```bash
# Dashboard on public server, data from GitHub
python app.py --host 0.0.0.0 --port 8080 --production
# Access from: http://public-server:8080
```

**Option 3: Local Development**
```bash
# Dashboard with local data (no GitHub)
python app.py --use-local
```

### Command Line Flags

```bash
--host HOST              # Host to bind to (default: 127.0.0.1)
--port PORT              # Port to run on (default: 5000)
--base-dir DIR           # Base directory for CI logs
--debug                  # Run in debug mode
--use-github             # Fetch from GitHub (default)
--use-local              # Force local filesystem only
```

### Environment Variables

```bash
export DASHBOARD_HOST=0.0.0.0
export DASHBOARD_PORT=5000
export USE_GITHUB=true              # Fetch from GitHub (default)
export GITHUB_REPO=ROCm/sglang-ci   # GitHub repository
```

## How It Works: GitHub Integration

### 1. Data Fetching Strategy

The dashboard uses a smart fetching strategy:

```python
# For each API request:
1. Try to fetch from GitHub raw URL
   └─ Success: Use GitHub data
   └─ Fail: Try local fallback

2. Parse content (same logic for both sources)

3. Return results to frontend
```

### 2. GitHub URLs Used

**For Logs:**
```
https://raw.githubusercontent.com/ROCm/sglang-ci/log/cron_log/mi30x/20251106/grok_nightly.log
```

**For Directory Listing:**
```
https://api.github.com/repos/ROCm/sglang-ci/contents/cron_log/mi30x?ref=log
```

**For Plots:**
```
https://raw.githubusercontent.com/ROCm/sglang-ci/log/plot/mi30x/GROK1/online/20251106_GROK1_online_standard.png
```

### 3. Benefits

- **No Firewall Issues**: Access GitHub from anywhere
- **No VPN Required**: Public GitHub URLs
- **High Availability**: GitHub CDN is fast and reliable
- **Local Fallback**: Still works if GitHub is down
- **Easy Deployment**: Host dashboard anywhere
- **No Authentication**: Public repo, no tokens needed

## Testing

### Verify Installation

```bash
# Check Python and dependencies
python3 --version
python3 -c "import flask; print('Flask OK')"
python3 -c "import requests; print('Requests OK')"

# Test help
python3 app.py --help
```

### Test Dashboard

```bash
# Start dashboard in foreground
python3 app.py --debug

# In another terminal, test API
curl http://localhost:5000/health
curl http://localhost:5000/api/dates/mi30x
curl http://localhost:5000/api/summary/mi30x/20251106
```

### Test GitHub Mode

```bash
# Explicitly use GitHub
python3 app.py --use-github --debug

# Verify in logs:
# "📡 Data Source: GitHub (with local fallback)"
```

## Performance Considerations

### GitHub API Rate Limits

- **Unauthenticated**: 60 requests/hour per IP
- **Authenticated**: 5000 requests/hour (not needed for public repos)

**Our Usage**: ~10-20 requests per page load
- Dashboard can serve ~3-6 page loads per hour (unauthenticated)
- For production, consider:
  1. Caching responses (add Redis/memcached)
  2. Using authenticated requests (higher limits)
  3. Local fallback reduces GitHub dependency

### Optimization Tips

1. **Cache GitHub responses**:
   - Add Flask-Caching
   - Cache directory listings (5-10 minutes)
   - Cache log content (1 hour)

2. **Use local fallback intelligently**:
   - If GitHub rate limited, auto-switch to local
   - Periodic sync from GitHub to local

3. **Connection pooling**:
   - Already implemented via `requests.Session()`
   - Reuses TCP connections

## Monitoring and Debugging

### Check Dashboard Status

```bash
# View logs
tail -f dashboard.log

# Check if running
ps aux | grep "app.py"

# Check port
lsof -i :5000
```

### Debug GitHub Issues

```python
# Test GitHub connectivity
import requests
url = "https://api.github.com/repos/ROCm/sglang-ci/contents/cron_log/mi30x?ref=log"
response = requests.get(url)
print(response.status_code, response.json())

# Test raw content
url = "https://raw.githubusercontent.com/ROCm/sglang-ci/log/cron_log/mi30x/20251106/grok_nightly.log"
response = requests.get(url)
print(response.status_code, len(response.text))
```

## Next Steps / Future Enhancements

1. **Add Caching**: Reduce GitHub API calls
2. **Add Authentication**: Support GitHub tokens for higher rate limits
3. **Add Webhooks**: Auto-refresh when new logs uploaded
4. **Add Comparison View**: Compare different dates/hardware side-by-side
5. **Add Export**: Download reports as PDF/CSV
6. **Add Notifications**: Alert when tests fail
7. **Add User Preferences**: Save favorite views, date ranges

## Conclusion

The dashboard successfully solves the firewall problem by:
- ✅ Fetching data from GitHub instead of direct server access
- ✅ Working from any location (laptop, public server, etc.)
- ✅ Providing comprehensive CI analytics and visualization
- ✅ Maintaining compatibility with existing CI infrastructure
- ✅ Including local fallback for reliability

**No changes required to existing CI scripts** - the dashboard integrates seamlessly with `github_log_upload.sh` which already uploads logs to GitHub.
