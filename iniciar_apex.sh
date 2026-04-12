#!/bin/bash
lsof -ti:5001 | xargs kill -9 2>/dev/null
pkill -f ngrok 2>/dev/null
sleep 2
python3 ~/Desktop/clone/mi-proyecto/nexus_server_elite.py &
sleep 3
ngrok http 5001 &
sleep 3
open /Users/christianvalareso/Downloads/nexus_apex.html
