#!/usr/bin/env python3
import subprocess
import os
import sys

def run_cmd(cmd, description=""):
    """Run a shell command and report success/failure"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"✅ {description}")
            if result.stdout:
                print(result.stdout)
            return True
        else:
            print(f"❌ {description}")
            print(f"Error: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error running: {e}")
        return False

# Change to repo directory
os.chdir("/home/runner/workspace")

print("=" * 60)
print("🤖 AUTO PUSH TO GITHUB - Trading Bot")
print("=" * 60)

# Step 1: Configure Git
run_cmd('git config --global user.name "Trading Bot"', "Configuring Git user")
run_cmd('git config --global user.email "bot@tradingbot.dev"', "Configuring Git email")

# Step 2: Add all changes
run_cmd('git add -A', "Adding all changes")

# Step 3: Show what's being committed
run_cmd('git status', "Checking status")

# Step 4: Commit
run_cmd('git commit -m "Update: 30m SAR SHORT-only strategy with dynamic TOP 1 gainer, Railway deployment ready"', "Committing changes")

# Step 5: Pull latest from remote
run_cmd('git pull origin main --rebase 2>/dev/null', "Pulling latest from GitHub")

# Step 6: Push to GitHub
success = run_cmd('GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no" git push origin main', "Pushing to GitHub")

print("=" * 60)
if success:
    print("✅✅✅ SUCCESS! Code pushed to GitHub!")
    print("🚀 Railway will auto-deploy the new version!")
    print("📊 Dashboard will update at: mexctradingbotv10-pocketoptionbotv10.up.railway.app")
else:
    print("⚠️  Push encountered issues")
    print("Try manually: git push origin main")
print("=" * 60)
