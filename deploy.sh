#!/bin/bash
#
# PiGenny Deployment Script
#
# Deploys monitor.py and tools to Raspberry Pi, and gen_server.py and tools to Olimex.
# Handles service restarts, verification, and automatic retry on failure.
#
# Usage: ./deploy.sh [OPTIONS]
#
# Options:
#   --skip-pi       Skip Pi deployment (only deploy to Olimex)
#   --skip-olimex   Skip Olimex deployment (only deploy to Pi)
#   --force         Deploy even if generator is running (DANGEROUS!)
#
# Prerequisites:
#   - SSH key authentication to Pi configured (~/.ssh/momspi)
#   - sshpass installed on Pi for Olimex access
#   - Current directory is pigenny/
#

set -e

# Configuration
PI_HOST="momspi.local"
PI_USER="derekja"
PI_SSH_KEY="$HOME/.ssh/momspi"
OLIMEX_IP="10.2.242.109"
OLIMEX_USER="derekja"
OLIMEX_PASS="Login123"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Options
SKIP_PI=0
SKIP_OLIMEX=0
FORCE=0

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-pi)
            SKIP_PI=1
            shift
            ;;
        --skip-olimex)
            SKIP_OLIMEX=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--skip-pi] [--skip-olimex] [--force]"
            exit 1
            ;;
    esac
done

function log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

function log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

function log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

function log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

function ssh_pi() {
    ssh -i "$PI_SSH_KEY" "${PI_USER}@${PI_HOST}" "$@"
}

function ssh_olimex() {
    ssh_pi "sshpass -p '$OLIMEX_PASS' ssh -o StrictHostKeyChecking=no ${OLIMEX_USER}@${OLIMEX_IP} '$@'"
}

function check_generator_stopped() {
    log_info "Checking if generator is stopped..."

    local status=$(ssh_pi "python3 -c \"
import socket
try:
    s = socket.socket()
    s.settimeout(3)
    s.connect(('$OLIMEX_IP', 9999))
    s.recv(1024)
    s.send(b'STATUS\n')
    data = b''
    while b'END' not in data:
        chunk = s.recv(1024)
        if not chunk: break
        data += chunk
    s.close()
    for line in data.decode().split('\n'):
        if 'RUNNING:' in line:
            print(line.split(':')[1].strip())
            break
except:
    print('ERROR')
\"" 2>/dev/null)

    if [ "$status" = "YES" ]; then
        log_error "Generator is RUNNING! Cannot deploy while generator is active."
        if [ $FORCE -eq 0 ]; then
            log_error "Use --force to override (will immediately shut down generator!)"
            return 1
        else
            log_warning "Proceeding anyway due to --force flag"
        fi
    elif [ "$status" = "NO" ]; then
        log_success "Generator is stopped, safe to deploy"
    else
        log_warning "Could not determine generator status (may be stopped)"
    fi

    return 0
}

function deploy_to_pi() {
    log_info "=========================================="
    log_info "  Deploying to Raspberry Pi"
    log_info "=========================================="

    # Copy files
    log_info "Copying monitor.py to Pi..."
    scp -i "$PI_SSH_KEY" monitor.py "${PI_USER}@${PI_HOST}:/home/derekja/pigenny/" || {
        log_error "Failed to copy monitor.py"
        return 1
    }

    log_info "Copying genserverstatus.py to Pi..."
    scp -i "$PI_SSH_KEY" genserverstatus.py "${PI_USER}@${PI_HOST}:/home/derekja/pigenny/" || {
        log_error "Failed to copy genserverstatus.py"
        return 1
    }

    # Set permissions and restart service
    log_info "Setting permissions and restarting service..."
    ssh_pi "chmod +x /home/derekja/pigenny/genserverstatus.py && sudo systemctl restart pigenny" || {
        log_error "Failed to restart pigenny service"
        return 1
    }

    # Wait for service to start
    log_info "Waiting for service to start..."
    sleep 5

    # Verify service is running
    log_info "Verifying service status..."
    local status=$(ssh_pi "systemctl is-active pigenny" 2>/dev/null)

    if [ "$status" = "active" ]; then
        log_success "Pi deployment complete - service is active"
        return 0
    else
        log_error "Service failed to start (status: $status)"
        log_info "Recent logs:"
        ssh_pi "journalctl -u pigenny -n 10 --no-pager"
        return 1
    fi
}

function deploy_to_olimex() {
    log_info "=========================================="
    log_info "  Deploying to Olimex"
    log_info "=========================================="

    # Copy files to Pi staging area
    log_info "Copying files to Pi staging area..."
    scp -i "$PI_SSH_KEY" gen_server.py update_genserver.py genserverstatus.py \
        "${PI_USER}@${PI_HOST}:/tmp/" || {
        log_error "Failed to copy files to Pi"
        return 1
    }

    # Copy files from Pi to Olimex
    log_info "Copying files to Olimex..."
    ssh_pi "sshpass -p '$OLIMEX_PASS' scp -o StrictHostKeyChecking=no \
        /tmp/gen_server.py /tmp/update_genserver.py /tmp/genserverstatus.py \
        ${OLIMEX_USER}@${OLIMEX_IP}:/home/derekja/" || {
        log_error "Failed to copy files to Olimex"
        return 1
    }

    # Install maintenance tools
    log_info "Installing maintenance tools on Olimex..."
    ssh_olimex "echo $OLIMEX_PASS | sudo -S cp /home/derekja/update_genserver.py /usr/local/bin/ && \
                sudo chmod +x /usr/local/bin/update_genserver.py && \
                sudo cp /home/derekja/genserverstatus.py /usr/local/bin/ && \
                sudo chmod +x /usr/local/bin/genserverstatus.py" 2>&1 | grep -v "password for" || {
        log_error "Failed to install tools"
        return 1
    }

    log_success "Maintenance tools installed"

    # Deploy gen_server.py using update_genserver.py
    log_info "Deploying gen_server.py using update script..."
    local update_result=$(ssh_olimex "echo $OLIMEX_PASS | sudo -S python2 /usr/local/bin/update_genserver.py --source /home/derekja/gen_server.py 2>&1" | grep -v "password for")

    if echo "$update_result" | grep -q "Update completed successfully"; then
        log_success "gen_server.py deployed successfully"
    elif echo "$update_result" | grep -q "WARNING.*not responding"; then
        log_warning "Update script reports server not responding, trying reboot..."
        return 2  # Signal that reboot is needed
    else
        log_warning "Update script result unclear, trying reboot..."
        return 2  # Signal that reboot is needed
    fi

    return 0
}

function reboot_olimex() {
    log_info "Rebooting Olimex..."
    ssh_olimex "echo $OLIMEX_PASS | sudo -S reboot" 2>&1 | grep -v "password for" || true

    log_info "Waiting for Olimex to reboot (30 seconds)..."
    sleep 30

    # Try to reconnect
    log_info "Waiting for Olimex to come back online..."
    for i in {1..12}; do
        if ssh_olimex "echo online" >/dev/null 2>&1; then
            log_success "Olimex is back online"
            sleep 3  # Give gen_server time to start
            return 0
        fi
        echo -n "."
        sleep 5
    done

    log_error "Olimex did not come back online after reboot"
    return 1
}

function verify_olimex() {
    log_info "Verifying Olimex deployment..."

    # Check if gen_server process is running
    local ps_result=$(ssh_olimex "ps aux | grep 'python2.*gen_server' | grep -v grep" 2>/dev/null)
    if [ -z "$ps_result" ]; then
        log_error "gen_server.py process not running"
        return 1
    fi

    log_success "gen_server.py process is running"

    # Query status using genserverstatus.py from Pi
    log_info "Querying status..."
    local status=$(ssh_pi "python3 /home/derekja/pigenny/genserverstatus.py --host $OLIMEX_IP --format compact" 2>/dev/null)

    if [ $? -eq 0 ]; then
        log_success "Status query successful:"
        echo "    $status"
        return 0
    else
        log_error "Failed to query status"
        return 1
    fi
}

function main() {
    log_info "=========================================="
    log_info "  PiGenny Deployment Script"
    log_info "=========================================="
    echo ""

    # Verify we're in the right directory
    if [ ! -f "monitor.py" ] || [ ! -f "gen_server.py" ]; then
        log_error "Must run from pigenny/ directory"
        exit 1
    fi

    # Check if generator is stopped (only if deploying to Olimex)
    if [ $SKIP_OLIMEX -eq 0 ]; then
        if ! check_generator_stopped; then
            exit 1
        fi
        echo ""
    fi

    # Deploy to Pi
    if [ $SKIP_PI -eq 0 ]; then
        if ! deploy_to_pi; then
            log_error "Pi deployment failed"
            exit 1
        fi
        echo ""
    else
        log_info "Skipping Pi deployment"
        echo ""
    fi

    # Deploy to Olimex
    if [ $SKIP_OLIMEX -eq 0 ]; then
        deploy_to_olimex
        olimex_result=$?

        if [ $olimex_result -eq 2 ]; then
            # Need to reboot
            if ! reboot_olimex; then
                log_error "Olimex reboot failed"
                exit 1
            fi
        elif [ $olimex_result -ne 0 ]; then
            log_error "Olimex deployment failed"
            exit 1
        fi

        echo ""

        # Verify Olimex deployment
        if ! verify_olimex; then
            log_error "Olimex verification failed"
            exit 1
        fi
        echo ""
    else
        log_info "Skipping Olimex deployment"
        echo ""
    fi

    # Final summary
    log_success "=========================================="
    log_success "  Deployment Complete!"
    log_success "=========================================="
    echo ""

    if [ $SKIP_PI -eq 0 ]; then
        log_info "Pi Status:"
        local pi_status=$(ssh_pi "systemctl is-active pigenny" 2>/dev/null)
        echo "    Service: $pi_status"
    fi

    if [ $SKIP_OLIMEX -eq 0 ]; then
        log_info "Olimex Status:"
        ssh_pi "python3 /home/derekja/pigenny/genserverstatus.py --host $OLIMEX_IP --format compact 2>/dev/null" | sed 's/^/    /'
    fi

    echo ""
    log_info "Monitor health checks with:"
    log_info "  ssh -i ~/.ssh/momspi derekja@momspi.local \"journalctl -u pigenny | grep 'Olimex health'\""
    echo ""
}

# Run main function
main
