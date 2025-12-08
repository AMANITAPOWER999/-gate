#!/usr/bin/env python3
"""
Deploy Trading Bot to GitHub and trigger Railway deployment
This script handles all git operations to push code to GitHub
"""

import subprocess
import sys
import os

def run(cmd, description=""):
    """Execute shell command"""
    print(f"\n{'='*60}")
    print(f"🔄 {description}")
    print(f"{'='*60}")
    print(f"Command: {cmd}\n")
    
    result = subprocess.run(cmd, shell=True, cwd="/home/runner/workspace")
    
    if result.returncode == 0:
        print(f"\n✅ SUCCESS: {description}")
        return True
    else:
        print(f"\n❌ FAILED: {description}")
        return False

def main():
    os.chdir("/home/runner/workspace")
    
    print("\n" + "="*60)
    print("🚀 TRADING BOT - DEPLOY TO GITHUB & RAILWAY")
    print("="*60)
    
    # Step 1: Configure git user
    run('git config user.name "Trading Bot Deployer"', "Configuring Git user")
    run('git config user.email "deploy@tradingbot.dev"', "Configuring Git email")
    
    # Step 2: Show current status
    run('git status', "Checking Git status")
    run('git log --oneline -5', "Recent commits")
    
    # Step 3: Configure origin remote (GitHub)
    run('git remote remove origin 2>/dev/null; true', "Removing old origin")
    run('git remote add origin git@github.com:manuninkirill-bot/tradingbot.git', "Adding GitHub origin")
    
    # Step 4: Push to GitHub
    cmd = 'GIT_SSH_COMMAND="ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no" git push -f origin main'
    
    print("\n" + "="*60)
    print("📤 PUSHING TO GITHUB...")
    print("="*60)
    
    result = subprocess.run(cmd, shell=True, cwd="/home/runner/workspace", 
                          capture_output=True, text=True)
    
    print("STDOUT:", result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    
    if result.returncode == 0:
        print("\n" + "="*60)
        print("✅✅✅ SUCCESS! CODE PUSHED TO GITHUB!")
        print("="*60)
        print("\n🎉 What happens next:")
        print("   1. Railway webhook triggers automatically")
        print("   2. New build starts: mexctradingbotv10-pocketoptionbotv10.up.railway.app")
        print("   3. Deploy completes in ~2-3 minutes")
        print("   4. Dashboard updates with latest code")
        print("\n📊 Repository: https://github.com/manuninkirill-bot/tradingbot")
        return 0
    else:
        print("\n" + "="*60)
        print("❌ Push to GitHub encountered issues")
        print("="*60)
        print("\nTroubleshooting:")
        print("1. Check SSH key: ssh -T git@github.com")
        print("2. Verify remote: git remote -v")
        print("3. Check commits: git log --oneline -5")
        return 1

if __name__ == "__main__":
    sys.exit(main())
