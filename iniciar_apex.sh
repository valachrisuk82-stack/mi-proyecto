#!/bin/bash
echo "🚀 Iniciando NEXUS APEX..."

# Matar procesos anteriores
lsof -ti:5001 | xargs kill -9 2>/dev/null
sleep 1

# Exportar API key
export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-"CONFIGURA_TU_KEY_AQUI"}

# Arrancar servidor
cd /Users/christianvalareso/Desktop/clone/mi-proyecto
python3 nexus_server_elite.py &
sleep 4

# Abrir Firefox
open -a Firefox http://localhost:5001
echo "✅ NEXUS APEX corriendo en http://localhost:5001"
