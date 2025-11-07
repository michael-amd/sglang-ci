# Quick Start Guide - SGLang CI Dashboard

## 🚀 Get Started in 3 Steps

### Step 1: Install Dependencies

```bash
cd /mnt/raid/michael/sglang-ci/dashboard
pip3 install -r requirements.txt
```

### Step 2: Start Dashboard

```bash
python3 app.py
```

Output:
```
🚀 Starting SGLang CI Dashboard
📁 Base directory: /mnt/raid/michael/sglang-ci
🌐 Server: http://127.0.0.1:5000
🔗 GitHub Repo: ROCm/sglang-ci
📡 Data Source: GitHub (with local fallback)
```

### Step 3: Open Browser

Navigate to: **http://127.0.0.1:5000**

---

## 🌐 Deploy Anywhere

The dashboard fetches data from GitHub, so it works from anywhere:

### On Your Laptop

```bash
# Make it accessible from other devices
python3 app.py --host 0.0.0.0 --port 5000

# Access from: http://your-laptop-ip:5000
```

### On a Public Server

```bash
# Production mode with Gunicorn
bash start_dashboard.sh --production --host 0.0.0.0 --port 8080

# Access from: http://your-server:8080
```

### Behind a Firewall (mi35x)

No problem! The dashboard fetches data from GitHub, not from the mi35x machine directly.

---

## 📊 What You Get

- **Home Page**: Summary of both MI30X and MI35X
- **Hardware Views**: Detailed results per platform
- **Trends**: Historical charts (pass rates, accuracy, runtime)
- **Plots**: Performance benchmark visualizations
- **REST API**: Programmatic access to all data

---

## 🔧 Common Commands

```bash
# Start in foreground
python3 app.py

# Start in background
bash start_dashboard.sh --background

# Stop background
bash stop_dashboard.sh

# Custom port
python3 app.py --port 8080

# Production mode
bash start_dashboard.sh --production

# Force local filesystem (no GitHub)
python3 app.py --use-local

# Debug mode
python3 app.py --debug
```

---

## 🔍 Test It Works

```bash
# Check health
curl http://localhost:5000/health

# Get available dates for mi30x
curl http://localhost:5000/api/dates/mi30x

# Get summary for today
DATE=$(date +%Y%m%d)
curl http://localhost:5000/api/summary/mi30x/$DATE
```

---

## ❓ Troubleshooting

### Port already in use

```bash
# Use a different port
python3 app.py --port 8080

# Or kill the process using the port
kill $(lsof -t -i:5000)
```

### No data showing

- Check if logs exist in GitHub: https://github.com/ROCm/sglang-ci/tree/log
- Verify `github_log_upload.sh` is running in CI
- Try local fallback: `python3 app.py --use-local`

### Import errors

```bash
# Reinstall dependencies
pip3 install -r requirements.txt --force-reinstall
```

---

## 📚 More Information

- **Full Documentation**: See [README.md](README.md)
- **Architecture Details**: See [DASHBOARD_SUMMARY.md](DASHBOARD_SUMMARY.md)
- **CI Integration**: Uses data from `github_log_upload.sh`

---

## ✅ Success Criteria

You should see:
- ✅ Dashboard starts without errors
- ✅ Home page loads in browser
- ✅ Data appears for recent dates
- ✅ Plots display correctly
- ✅ Can switch between MI30X and MI35X

If you see all of the above, congratulations! The dashboard is working correctly.

---

## 🎉 You're Ready!

The dashboard is now ready to use. It automatically:
- Fetches the latest CI data from GitHub
- Falls back to local files if needed
- Updates in real-time as you refresh
- Works from anywhere (no firewall issues!)

Enjoy your new CI dashboard! 🚀
