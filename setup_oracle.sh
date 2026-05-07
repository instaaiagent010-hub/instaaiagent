#!/bin/bash
# Oracle Cloud VM Setup Script — Instagram News Agent

echo "=== Installing dependencies ==="
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip git

pip3 install ddgs groq requests Pillow python-dotenv

echo "=== Setup complete! ==="
echo "Ab .env file banao aur cron job lagao"
