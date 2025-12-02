#!/bin/bash
#
# Setup HTTPS for SGLang CI Dashboard
# 
# This script configures the dashboard to be accessible at:
#   https://sglang-internal.amd.com:3000/
#
# PREREQUISITES:
# 1. Ask AMD IT to create DNS A record:
#    sglang-internal.amd.com -> $(hostname -I | awk '{print $1}')
#
# 2. Get SSL certificate from AMD IT, OR use self-signed (for internal use)
#
# USAGE:
#   bash setup_https.sh              # Interactive setup
#   bash setup_https.sh --self-signed  # Use self-signed certificate
#   bash setup_https.sh --check      # Check current status
#

set -e

DOMAIN="sglang-internal.amd.com"
HTTPS_PORT=3000
FLASK_PORT=5000
CERT_DIR="/etc/ssl/sglang-dashboard"
DASHBOARD_DIR="/mnt/raid/michael/sglang-ci/dashboard"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_header() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}

check_status() {
    print_header "Current Status"
    
    echo -e "🖥️  Server IP: ${GREEN}$(hostname -I | awk '{print $1}')${NC}"
    echo -e "🏠 Hostname:  ${GREEN}$(hostname)${NC}"
    echo ""
    
    # Check DNS
    echo -n "🌐 DNS ($DOMAIN): "
    if host $DOMAIN &>/dev/null; then
        IP=$(host $DOMAIN | grep "has address" | head -1 | awk '{print $NF}')
        echo -e "${GREEN}$IP${NC}"
    else
        echo -e "${RED}Not configured${NC}"
        echo -e "   ${YELLOW}→ Ask AMD IT to add DNS A record:${NC}"
        echo -e "   ${YELLOW}  $DOMAIN -> $(hostname -I | awk '{print $1}')${NC}"
    fi
    
    # Check nginx
    echo -n "📦 Nginx:     "
    if which nginx &>/dev/null; then
        echo -e "${GREEN}Installed$(nginx -v 2>&1 | cut -d'/' -f2)${NC}"
    else
        echo -e "${YELLOW}Not installed${NC}"
    fi
    
    # Check SSL cert
    echo -n "🔐 SSL Cert:  "
    if [ -f "$CERT_DIR/server.crt" ]; then
        EXPIRY=$(openssl x509 -enddate -noout -in "$CERT_DIR/server.crt" | cut -d= -f2)
        echo -e "${GREEN}Found (expires: $EXPIRY)${NC}"
    else
        echo -e "${YELLOW}Not found${NC}"
    fi
    
    # Check dashboard
    echo -n "🚀 Dashboard: "
    if curl -s http://127.0.0.1:$FLASK_PORT/health &>/dev/null; then
        echo -e "${GREEN}Running on port $FLASK_PORT${NC}"
    else
        echo -e "${RED}Not running${NC}"
    fi
    
    # Check HTTPS endpoint
    echo -n "🔒 HTTPS:     "
    if curl -sk https://127.0.0.1:$HTTPS_PORT/health &>/dev/null; then
        echo -e "${GREEN}https://127.0.0.1:$HTTPS_PORT ✓${NC}"
    else
        echo -e "${YELLOW}Not configured${NC}"
    fi
    
    echo ""
}

install_nginx() {
    print_header "Installing Nginx"
    
    if which nginx &>/dev/null; then
        echo -e "${GREEN}Nginx already installed${NC}"
        return
    fi
    
    echo "Installing nginx..."
    sudo apt-get update
    sudo apt-get install -y nginx
    sudo systemctl enable nginx
    echo -e "${GREEN}✓ Nginx installed${NC}"
}

generate_self_signed_cert() {
    print_header "Generating Self-Signed Certificate"
    
    echo "Creating certificate directory..."
    sudo mkdir -p "$CERT_DIR"
    
    echo "Generating private key and certificate..."
    sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERT_DIR/server.key" \
        -out "$CERT_DIR/server.crt" \
        -subj "/C=US/ST=California/L=Santa Clara/O=AMD/OU=SGLang CI/CN=$DOMAIN" \
        -addext "subjectAltName=DNS:$DOMAIN,DNS:localhost,IP:$(hostname -I | awk '{print $1}')"
    
    sudo chmod 600 "$CERT_DIR/server.key"
    sudo chmod 644 "$CERT_DIR/server.crt"
    
    echo -e "${GREEN}✓ Self-signed certificate generated${NC}"
    echo -e "  Certificate: $CERT_DIR/server.crt"
    echo -e "  Private Key: $CERT_DIR/server.key"
    echo -e "\n${YELLOW}⚠️  Browsers will show security warning for self-signed certs${NC}"
}

configure_nginx() {
    print_header "Configuring Nginx"
    
    CONFIG_FILE="/etc/nginx/sites-available/sglang-dashboard"
    
    echo "Creating nginx configuration..."
    sudo tee "$CONFIG_FILE" > /dev/null << NGINX_CONF
# SGLang CI Dashboard - HTTPS Configuration
# URL: https://$DOMAIN:$HTTPS_PORT/

server {
    listen $HTTPS_PORT ssl;
    listen [::]:$HTTPS_PORT ssl;
    server_name $DOMAIN $(hostname -I | awk '{print $1}') localhost;

    # SSL Configuration
    ssl_certificate $CERT_DIR/server.crt;
    ssl_certificate_key $CERT_DIR/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;

    # Proxy to Flask Dashboard
    location / {
        proxy_pass http://127.0.0.1:$FLASK_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # WebSocket support (if needed)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Health check endpoint (bypass proxy for quick checks)
    location /nginx-health {
        return 200 'healthy';
        add_header Content-Type text/plain;
    }
}
NGINX_CONF

    echo "Enabling site..."
    sudo ln -sf "$CONFIG_FILE" /etc/nginx/sites-enabled/
    
    echo "Testing nginx configuration..."
    sudo nginx -t
    
    echo "Reloading nginx..."
    sudo systemctl reload nginx
    
    echo -e "${GREEN}✓ Nginx configured${NC}"
}

create_systemd_service() {
    print_header "Creating Systemd Service"
    
    SERVICE_FILE="/etc/systemd/system/sglang-dashboard.service"
    
    echo "Creating systemd service..."
    sudo tee "$SERVICE_FILE" > /dev/null << SERVICE
[Unit]
Description=SGLang CI Dashboard
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DASHBOARD_DIR
Environment="SGL_BENCHMARK_CI_DIR=/mnt/raid/michael/sglang-ci"
Environment="USE_DATABASE=true"
ExecStart=/usr/bin/python3 $DASHBOARD_DIR/app.py --host 127.0.0.1 --port $FLASK_PORT
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

    echo "Reloading systemd..."
    sudo systemctl daemon-reload
    
    echo "Enabling service..."
    sudo systemctl enable sglang-dashboard
    
    echo "Starting service..."
    sudo systemctl start sglang-dashboard
    
    echo -e "${GREEN}✓ Systemd service created and started${NC}"
    echo -e "  Status: sudo systemctl status sglang-dashboard"
    echo -e "  Logs:   sudo journalctl -u sglang-dashboard -f"
}

full_setup() {
    print_header "Full HTTPS Setup for SGLang Dashboard"
    
    echo -e "This will configure:"
    echo -e "  📍 URL: https://$DOMAIN:$HTTPS_PORT/"
    echo -e "  🖥️  Server: $(hostname -I | awk '{print $1}')"
    echo ""
    
    # Step 1: Install nginx
    install_nginx
    
    # Step 2: Generate/check SSL certificate
    if [ ! -f "$CERT_DIR/server.crt" ] || [ "$1" == "--self-signed" ]; then
        generate_self_signed_cert
    fi
    
    # Step 3: Configure nginx
    configure_nginx
    
    # Step 4: Create systemd service (optional)
    echo ""
    read -p "Create systemd service for auto-start? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        create_systemd_service
    fi
    
    print_header "Setup Complete!"
    
    echo -e "🌐 Dashboard URL: ${GREEN}https://$DOMAIN:$HTTPS_PORT/${NC}"
    echo -e "   (or https://$(hostname -I | awk '{print $1}'):$HTTPS_PORT/)"
    echo ""
    echo -e "${YELLOW}IMPORTANT: DNS Configuration Required${NC}"
    echo -e "Ask AMD IT to create DNS A record:"
    echo -e "  $DOMAIN -> $(hostname -I | awk '{print $1}')"
    echo ""
    echo -e "Commands:"
    echo -e "  Check status:  bash $0 --check"
    echo -e "  View logs:     sudo journalctl -u sglang-dashboard -f"
    echo -e "  Restart:       sudo systemctl restart sglang-dashboard nginx"
}

# Main
case "${1:-}" in
    --check)
        check_status
        ;;
    --self-signed)
        full_setup --self-signed
        ;;
    --cert-only)
        generate_self_signed_cert
        ;;
    --nginx-only)
        install_nginx
        configure_nginx
        ;;
    --help|-h)
        echo "Usage: $0 [option]"
        echo ""
        echo "Options:"
        echo "  (no option)    Interactive full setup"
        echo "  --check        Check current status"
        echo "  --self-signed  Full setup with self-signed certificate"
        echo "  --cert-only    Generate self-signed certificate only"
        echo "  --nginx-only   Install and configure nginx only"
        echo "  --help         Show this help"
        ;;
    *)
        full_setup
        ;;
esac

