# SGLang CI Dashboard

A comprehensive web dashboard for viewing CI results, performance plots, and trends across MI30X and MI35X hardware platforms.

## Features

- **Daily Summary View**: Overview of all nightly CI tasks for both hardware platforms
- **Hardware-Specific Views**: Detailed results for MI30X and MI35X
- **Historical Trends**: Visualize pass rates, GSM8K accuracy, and runtime trends over time
- **Performance Plots**: View and download performance benchmark plots
- **REST API**: Programmatic access to all CI data
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **GitHub Data Source**: Fetch data from GitHub log branch (works behind firewalls!)
- **Local Fallback**: Automatically falls back to local filesystem if GitHub is unavailable

## Architecture

The dashboard reuses data collection logic from `send_daily_summary_alert.py` and extends it with:
- Flask web framework for HTTP server
- Bootstrap 5 for modern, responsive UI
- Chart.js for interactive trend visualizations
- RESTful API for data access

## Installation

### Prerequisites

- Python 3.8 or higher
- Access to SGLang CI log directory (default: `/mnt/raid/michael/sglang-ci`)

### Install Dependencies

```bash
cd /mnt/raid/michael/sglang-ci/dashboard
pip install -r requirements.txt
```

## Usage

### Quick Start

Run the dashboard with default settings (uses GitHub by default):

```bash
cd /mnt/raid/michael/sglang-ci/dashboard
python app.py
```

Then open your browser to: http://127.0.0.1:5000

**Note**: By default, the dashboard fetches data from the GitHub log branch (`https://github.com/ROCm/sglang-ci/tree/log`). This allows the dashboard to work even when the server is behind a firewall, as is the case with mi35x machines. The dashboard automatically falls back to local filesystem if GitHub is unavailable.

### Command Line Options

```bash
# Bind to specific host and port
python app.py --host 0.0.0.0 --port 8080

# Use custom CI log directory
python app.py --base-dir /path/to/sglang-ci

# Run in debug mode (for development)
python app.py --debug

# Force use of local filesystem only (disable GitHub)
python app.py --use-local

# Explicitly enable GitHub mode (default)
python app.py --use-github
```

### Using the Startup Script

For easier deployment, use the provided startup script:

```bash
# Start dashboard (foreground)
bash start_dashboard.sh

# Start dashboard on custom port
bash start_dashboard.sh --port 8080

# Start dashboard in production mode
bash start_dashboard.sh --production

# Start dashboard in background
bash start_dashboard.sh --background

# Stop background dashboard
bash stop_dashboard.sh
```

### Environment Variables

Configure the dashboard using environment variables:

- `DASHBOARD_HOST`: Host to bind to (default: `127.0.0.1`)
- `DASHBOARD_PORT`: Port to run on (default: `5000`)
- `SGL_BENCHMARK_CI_DIR`: Base directory for CI logs (default: `/mnt/raid/michael/sglang-ci`)
- `GITHUB_REPO`: GitHub repository (default: `ROCm/sglang-ci`)
- `USE_GITHUB`: Use GitHub as data source (default: `true`)

Example:

```bash
export DASHBOARD_HOST=0.0.0.0
export DASHBOARD_PORT=8080
export SGL_BENCHMARK_CI_DIR=/custom/path/to/sglang-ci
export USE_GITHUB=true  # Fetch from GitHub (default)
python app.py

# Or use local filesystem only
export USE_GITHUB=false
python app.py
```

## Production Deployment

### Using Gunicorn (Recommended)

For production environments, use Gunicorn as the WSGI server:

```bash
# Install gunicorn (included in requirements.txt)
pip install gunicorn

# Run with 4 worker processes
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Or use the startup script
bash start_dashboard.sh --production
```

### Using Systemd Service

Create a systemd service file `/etc/systemd/system/sglang-dashboard.service`:

```ini
[Unit]
Description=SGLang CI Dashboard
After=network.target

[Service]
Type=simple
User=michael
WorkingDirectory=/mnt/raid/michael/sglang-ci/dashboard
Environment="PATH=/usr/bin:/usr/local/bin"
Environment="SGL_BENCHMARK_CI_DIR=/mnt/raid/michael/sglang-ci"
ExecStart=/usr/bin/python3 app.py --host 0.0.0.0 --port 5000
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable sglang-dashboard
sudo systemctl start sglang-dashboard
sudo systemctl status sglang-dashboard
```

## Dashboard Pages

### Home (`/`)
- Summary cards for both MI30X and MI35X
- Hardware comparison chart
- Recent activity table

### Hardware View (`/hardware/<hardware>`)
- Overall summary statistics
- Performance benchmarks
- Integration tests
- Validation & checks
- Sanity check (accuracy) results

### Trends (`/trends`)
- Overall pass rate trends
- Task status distribution over time
- GSM8K accuracy trends
- Runtime trends

### Plots (`/plots/<hardware>`)
- Performance benchmark plots
- Direct links to GitHub
- Download options

## REST API

The dashboard provides REST API endpoints for programmatic access:

### Get Daily Summary

```bash
GET /api/summary/<hardware>/<date>

# Example
curl http://localhost:5000/api/summary/mi30x/20251106
```

### Get Historical Trends

```bash
GET /api/trends/<hardware>?days=<days>

# Example
curl http://localhost:5000/api/trends/mi30x?days=30
```

### Get Available Dates

```bash
GET /api/dates/<hardware>

# Example
curl http://localhost:5000/api/dates/mi30x
```

### Get Available Plots

```bash
GET /api/plots/<hardware>/<date>

# Example
curl http://localhost:5000/api/plots/mi30x/20251106
```

### Compare Hardware

```bash
GET /api/compare?date=<date>

# Example
curl http://localhost:5000/api/compare?date=20251106
```

### Health Check

```bash
GET /health

# Example
curl http://localhost:5000/health
```

## Directory Structure

```
dashboard/
├── app.py                    # Main Flask application
├── data_collector.py         # Data collection module
├── requirements.txt          # Python dependencies
├── README.md                # This file
├── start_dashboard.sh       # Startup script
├── stop_dashboard.sh        # Stop script
├── templates/               # HTML templates
│   ├── base.html           # Base template
│   ├── index.html          # Home page
│   ├── hardware.html       # Hardware-specific view
│   ├── trends.html         # Trends page
│   ├── plots.html          # Plots viewer
│   ├── 404.html            # 404 error page
│   └── 500.html            # 500 error page
└── static/                  # Static assets
    ├── css/
    │   └── style.css       # Custom CSS
    └── js/
        └── dashboard.js    # JavaScript utilities
```

## Development

### Running in Debug Mode

```bash
python app.py --debug
```

Debug mode enables:
- Auto-reload on code changes
- Detailed error pages
- Flask debugger

### Adding New Features

1. **New API Endpoint**: Add route in `app.py`
2. **New Data Collection**: Extend `data_collector.py`
3. **New UI Component**: Update templates in `templates/`
4. **New Styling**: Update `static/css/style.css`
5. **New JavaScript**: Update `static/js/dashboard.js`

## Troubleshooting

### Port Already in Use

If you get "Address already in use" error:

```bash
# Find process using the port
lsof -i :5000

# Kill the process
kill $(lsof -t -i:5000)

# Or use a different port
python app.py --port 8080
```

### Permission Denied

If you get permission errors accessing logs:

```bash
# Check directory permissions
ls -la /mnt/raid/michael/sglang-ci/cron/cron_log

# Run with appropriate user
sudo -u michael python app.py
```

### No Data Showing

If the dashboard shows no data:

1. Check that CI logs exist in the expected directory
2. Verify the date format (YYYYMMDD)
3. Check console for error messages (if debug mode)
4. Verify API endpoints: `curl http://localhost:5000/api/dates/mi30x`

### Import Errors

If you get import errors:

```bash
# Make sure you're in the correct directory
cd /mnt/raid/michael/sglang-ci/dashboard

# Reinstall dependencies
pip install -r requirements.txt

# Check Python path
python -c "import sys; print(sys.path)"
```

## Integration with Existing CI

The dashboard integrates seamlessly with existing CI infrastructure:

- **Logs**: Reads from GitHub `log` branch via `github_log_upload.sh` (with local fallback)
- **Plots**: Links to GitHub `log/plot/<hardware>/`
- **Data**: Reuses `send_daily_summary_alert.py` parsing logic
- **No Modifications**: No changes to existing CI scripts required
- **Firewall Friendly**: Works with mi35x and other machines behind firewalls

### How GitHub Integration Works

1. **CI runs nightly tasks** and generates logs locally
2. **`github_log_upload.sh`** automatically uploads logs to the GitHub `log` branch
3. **Dashboard fetches data** from GitHub raw URLs (no authentication needed for public repos)
4. **Local fallback** ensures dashboard works even if GitHub is down
5. **Plots are served** directly from GitHub raw URLs

This architecture allows the dashboard to be hosted anywhere (including behind firewalls) while still accessing the latest CI data.

## Contributing

When contributing to the dashboard:

1. Follow existing code style (use Black for Python, 120 cols)
2. Add comments for complex logic
3. Update this README if adding new features
4. Test on both MI30X and MI35X data
5. Ensure responsive design works on mobile

## Support

For issues or questions:

1. Check the troubleshooting section above
2. Review Flask logs for error details
3. Check GitHub issues: https://github.com/ROCm/sglang-ci/issues
4. Contact the team via Teams or GitHub

## License

This dashboard is part of the SGLang CI toolkit and follows the same license as the main repository.
